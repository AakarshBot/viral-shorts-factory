import streamlit as st
import ultimate_bot 

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬")
st.title("🎬 Viral Shorts Factory")
st.write("Configure and launch your YouTube Shorts automation.")

# 1. Pipeline Selection (Mirroring your CLI options)
pipeline_choice = st.radio(
    "Select Factory Pipeline:", 
    ["Manual Mode", "Auto-Pilot Mode (AI Selection)", "Cricket Focus Pipeline"]
)

# 2. Dynamic Inputs based on Pipeline
web_config = {}

if pipeline_choice == "Manual Mode":
    format_choice = st.selectbox("Format:", ["Regular Deep-Dive", "Top 5 Countdown", "Trending Now"])
    format_map = {"Regular Deep-Dive": "regular", "Top 5 Countdown": "top5", "Trending Now": "trending"}
    
    # Dynamically pull exactly what is inside ultimate_bot.py
    category_options = {val["label"]: key for key, val in ultimate_bot.CONTENT_CATEGORIES.items()}
    
    if format_choice != "Trending Now":
        cat_friendly = st.selectbox("Category:", list(category_options.keys()))
        selected_cat_key = category_options[cat_friendly]
    else:
        st.info("📈 Trending Now selected: The bot will auto-fetch the hottest topic from Google Trends.")
        selected_cat_key = "national_global_affairs"
        
    language_options = {val["label"]: key for key, val in ultimate_bot.LANGUAGES.items()}
    lang_friendly = st.selectbox("Language:", list(language_options.keys()))
    
    web_config = {
        "format_mode": format_map[format_choice],
        "category": selected_cat_key,
        "language": language_options[lang_friendly]
    }

elif pipeline_choice == "Auto-Pilot Mode (AI Selection)":
    st.info("🤖 The factory will use Epsilon-Greedy logic to automatically select the best performing format, category, and language based on your historical vault database.")
    web_config = None # Passing None triggers your bot's Auto-Pilot

elif pipeline_choice == "Cricket Focus Pipeline":
    cricket_sub = st.selectbox("Cricket Sub-Genre:", [
        "Asian Giants Focus (BCCI, PCB, SLC, BCB, ACB)",
        "Global & Test Nation Elite (ICC, Ashes, BGT)",
        "⚡ AI Auto-Detect (Scans live cricket trends)"
    ])
    
    format_choice = st.selectbox("Format:", ["Regular Deep-Dive", "Top 5 Countdown"])
    format_map = {"Regular Deep-Dive": "regular", "Top 5 Countdown": "top5"}
    
    language_options = {val["label"]: key for key, val in ultimate_bot.LANGUAGES.items()}
    lang_friendly = st.selectbox("Language:", list(language_options.keys()))
    
    web_config = {
        "format_mode": format_map[format_choice],
        "category": "sports_stories_of_day",
        "language": language_options[lang_friendly]
    }
    
    # Map the custom RSS/Queries based on your bot's exact cricket logic
    if "Asian Giants" in cricket_sub:
        web_config["custom_q"] = "India Cricket OR Pakistan Cricket OR Sri Lanka Cricket OR Bangladesh Cricket"
        web_config["custom_rss"] = "https://news.google.com/rss/search?q=India+Cricket+OR+Pakistan+Cricket+OR+BCCI&hl=en-IN&gl=IN&ceid=IN:en"
    elif "Global & Test" in cricket_sub:
        web_config["custom_q"] = "Test Cricket OR ICC OR Ashes OR Border Gavaskar Trophy OR Australia Cricket"
        web_config["custom_rss"] = "https://news.google.com/rss/search?q=Test+Cricket+OR+ICC+OR+Ashes&hl=en-IN&gl=IN&ceid=IN:en"

st.divider()
publish_choice = st.selectbox("YouTube Visibility:", ["Private", "Public"])

# 3. The Run Button
if st.button("🚀 Start The Factory", type="primary"):
    if web_config is not None:
        web_config["publish_mode"] = publish_choice.lower()
    
    st.info("⚙️ Factory is running! Check the Streamlit Cloud logs (bottom right corner '>_ Manage app') to see the live printout.")
    
    try:
        with st.spinner("Executing script generation, visual sourcing, and rendering. This will take a few minutes..."):
            ultimate_bot.run_robot(web_config=web_config)
        st.success("✅ Video successfully generated and uploaded!")
        st.balloons()
    except Exception as e:
        st.error(f"❌ An error occurred: {e}")