import os
import sqlite3
import time
import streamlit as st
import ultimate_bot
from db_architecture import migrate_vault
from db_runtime import run_robot_with_exact_identity
from factory_runtime import install_safe_exception_hook, normalise_publish_mode, patch_dashboard_runtime
from autopilot_runtime import select_auto_pilot
from story_ranker import patch_story_selection
from learning_runtime import sync_factory_analytics
from quality_runtime import patch_quality_control
from visual_runtime import patch_visual_pipeline

install_safe_exception_hook()
patch_dashboard_runtime(ultimate_bot)
patch_story_selection(ultimate_bot)
patch_quality_control(ultimate_bot)
patch_visual_pipeline(ultimate_bot)
ultimate_bot.token_overlap_ratio = lambda _a, _b: 0.0

# Replace the legacy topic-based/channel-wide analytics sweep with the exact
# video-ID learning sync. run_robot() resolves this global by name at runtime.
ultimate_bot.run_analytics_sweep = lambda conn: sync_factory_analytics(ultimate_bot, conn)

try:
    _db = sqlite3.connect(ultimate_bot.DB_PATH)
    migrate_vault(_db)
    _db.close()
except Exception as e:
    st.warning(f"Database migration check failed: {e}")

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬")
st.title("🎬 Viral Shorts Factory")
st.write("Configure and launch your YouTube Shorts automation.")

pipeline_choice = st.radio("Select Factory Pipeline:", ["Manual Mode", "Auto-Pilot Mode (AI Selection)", "Cricket Focus Pipeline"])
web_config = {}

# Analytics controls are deliberately separate from production. Syncing is
# explicit so a Streamlit page refresh can never unexpectedly trigger OAuth.
if st.button("📊 Sync YouTube Performance"):
    try:
        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        try:
            migrate_vault(conn)
            with st.spinner("Refreshing YouTube performance for factory-created Shorts..."):
                result = sync_factory_analytics(ultimate_bot, conn)
        finally:
            conn.close()
        st.success(
            f"Analytics sync complete: {result['updated']} videos refreshed; "
            f"{result['retention_ready']} now have retention data."
        )
    except Exception as e:
        st.error(f"❌ Analytics sync failed: {e}")

if pipeline_choice == "Manual Mode":
    format_choice = st.selectbox("Format:", ["Regular Deep-Dive", "Top 5 Countdown", "Trending Now"])
    format_map = {"Regular Deep-Dive": "regular", "Top 5 Countdown": "top5", "Trending Now": "trending"}
    category_options = {val["label"]: key for key, val in ultimate_bot.CONTENT_CATEGORIES.items()}
    if format_choice != "Trending Now":
        cat_friendly = st.selectbox("Category:", list(category_options.keys()))
        selected_cat_key = category_options[cat_friendly]
    else:
        st.info("📈 Trending Now selected: the bot will auto-fetch the hottest topic from Google Trends.")
        selected_cat_key = "national_global_affairs"
    language_options = {val["label"]: key for key, val in ultimate_bot.LANGUAGES.items()}
    lang_friendly = st.selectbox("Language:", list(language_options.keys()))
    web_config = {"format_mode": format_map[format_choice], "category": selected_cat_key, "language": language_options[lang_friendly]}

elif pipeline_choice == "Auto-Pilot Mode (AI Selection)":
    st.info("🤖 Auto-Pilot will select the best-supported Shorts format, category and language from relevant historical performance segments, while still testing under-used combinations.")
    try:
        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        try:
            migrate_vault(conn)
            format_mode, selected_cat_key, lang_cfg, combo_key = select_auto_pilot(ultimate_bot, conn)
        finally:
            conn.close()
        web_config = {"format_mode": format_mode, "category": selected_cat_key, "language": lang_cfg["key"], "combo_key": combo_key}
        st.caption(f"Auto-Pilot selected: **{format_mode}** · **{ultimate_bot.CONTENT_CATEGORIES[selected_cat_key]['label']}** · **{lang_cfg['label']}**")
    except Exception as e:
        st.warning(f"Auto-Pilot could not read the performance vault, so it will use safe defaults. Details: {e}")
        web_config = {"format_mode": "regular", "category": "national_global_affairs", "language": "english"}

elif pipeline_choice == "Cricket Focus Pipeline":
    cricket_sub = st.selectbox("Cricket Sub-Genre:", ["Asian Giants Focus (BCCI, PCB, SLC, BCB, ACB)", "Global & Test Nation Elite (ICC, Ashes, BGT)", "⚡ AI Auto-Detect (Scans live cricket trends)"])
    format_choice = st.selectbox("Format:", ["Regular Deep-Dive", "Top 5 Countdown"])
    format_map = {"Regular Deep-Dive": "regular", "Top 5 Countdown": "top5"}
    language_options = {val["label"]: key for key, val in ultimate_bot.LANGUAGES.items()}
    lang_friendly = st.selectbox("Language:", list(language_options.keys()))
    web_config = {"format_mode": format_map[format_choice], "category": "sports_stories_of_day", "language": language_options[lang_friendly]}
    if "Asian Giants" in cricket_sub:
        web_config["custom_q"] = "India Cricket OR Pakistan Cricket OR Sri Lanka Cricket OR Bangladesh Cricket"
        web_config["custom_rss"] = "https://news.google.com/rss/search?q=India+Cricket+OR+Pakistan+Cricket+OR+BCCI&hl=en-IN&gl=IN&ceid=IN:en"
    elif "Global & Test" in cricket_sub:
        web_config["custom_q"] = "Test Cricket OR ICC OR Ashes OR Border Gavaskar Trophy OR Australia Cricket"
        web_config["custom_rss"] = "https://news.google.com/rss/search?q=Test+Cricket+OR+ICC+OR+Ashes&hl=en-IN&gl=IN&ceid=IN:en"
    else:
        try:
            trends = ultimate_bot.fetch_trending_topics(target="india", query_filter="Cricket OR BCCI OR IPL OR ICC OR T20")
            web_config["trend_keyword"] = trends[0] if trends else "Cricket"
            st.caption(f"⚡ Auto-detected cricket trend: **{web_config['trend_keyword']}**")
        except Exception as e:
            web_config["trend_keyword"] = "Cricket"
            st.warning(f"Trend lookup failed, so the factory will search for Cricket. Details: {e}")

st.divider()
publish_choice = st.selectbox("YouTube Visibility:", ["Private", "Public"])

if st.button("🚀 Start The Factory", type="primary"):
    web_config["publish_mode"] = normalise_publish_mode(publish_choice)
    ultimate_bot._active_web_config = dict(web_config)
    video_path = os.path.join(ultimate_bot.ASSETS_DIR, "final_video_output.mp4")
    run_started_at = time.time()

    try:
        if os.path.exists(video_path):
            os.remove(video_path)
    except Exception as e:
        st.warning(f"Could not clear the previous output file: {e}")

    st.info("⚙️ Factory is running! Check the Streamlit Cloud logs (bottom right corner '>_ Manage app') for detailed progress.")
    try:
        with st.spinner("Executing script generation, visual sourcing, and rendering. This will take a few minutes..."):
            run_robot_with_exact_identity(ultimate_bot, web_config=web_config)
        fresh_video = os.path.exists(video_path) and os.path.getmtime(video_path) >= run_started_at
        if fresh_video:
            st.success(f"✅ Factory run completed and a fresh Short was generated. YouTube visibility was set to **{publish_choice}**. Check the logs for the upload result.")
        else:
            st.warning("⚠️ The factory stopped without producing a fresh final video. Check the logs above for the exact reason.")
    except Exception as e:
        st.error(f"❌ An error occurred: {e}")