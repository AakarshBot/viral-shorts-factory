import streamlit as st
import ultimate_bot 

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬")
st.title("🎬 Viral Shorts Factory")
st.write("Configure and launch your YouTube Shorts automation.")

# 1. Dashboard Controls
format_choice = st.selectbox("Format:", ["Regular Deep-Dive", "Top 5 Countdown", "Trending Now"])

category_map = {
    "Global & National Affairs": "national_global_affairs",
    "Sports Highlights": "sports_stories_of_day",
    "Movie & Entertainment Gossips": "entertainment",
    "Tech & AI News": "technology",
    "Business & Finance": "business_finance"
}
cat_friendly = st.selectbox("Category:", list(category_map.keys()))

language_choice = st.selectbox("Language:", ["English", "Hindi", "Telugu"])
publish_choice = st.selectbox("Visibility:", ["Private", "Public"])

format_map = {
    "Regular Deep-Dive": "regular",
    "Top 5 Countdown": "top5",
    "Trending Now": "trending"
}

# 2. The Run Button
if st.button("🚀 Start The Factory", type="primary"):
    # Build the exact config dictionary ultimate_bot.py expects
    web_config = {
        "format_mode": format_map[format_choice],
        "category": category_map[cat_friendly],
        "language": language_choice.lower(),
        "publish_mode": publish_choice.lower()
    }
    
    st.info("⚙️ Factory is running! Check the Streamlit Cloud logs (bottom right corner 'Manage app') to see the live printout.")
    
    # Trigger the actual bot
    try:
        with st.spinner("Executing script generation, visual sourcing, and rendering. This will take a few minutes..."):
            ultimate_bot.run_robot(web_config=web_config)
        st.success("✅ Video successfully generated and uploaded!")
    except Exception as e:
        st.error(f"❌ An error occurred: {e}")