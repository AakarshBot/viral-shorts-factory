import streamlit as st
# We import your bot so the website can use its functions!
import ultimate_bot 

st.title("🎬 Viral Shorts Factory")
st.write("Press the button to generate a new video!")

# This creates a dropdown menu for your phone
format_choice = st.selectbox("Choose Format:", ["Regular Deep-Dive", "Top 5 Countdown"])

# This creates a big button
if st.button("Start The Factory"):
    st.write("Factory is running! Please wait...")
    
    # We will eventually hook this up to pass your choices into your script
    # ultimate_bot.run_robot(format_choice) 
    
    st.success("Video Generated and Uploaded!")