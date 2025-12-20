import streamlit as st
import pandas as pd
import plotly.express as px
from googleapiclient.discovery import build
import concurrent.futures

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="YouTube Market Engine",
    page_icon="⚡",
    layout="wide"
)

# --- CSS STYLING ---
st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; font-weight: bold; }
    .metric-card { background-color: #f0f2f6; border-left: 5px solid #ff4b4b; padding: 15px; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR: SETTINGS ---
with st.sidebar:
    st.header("⚙️ Engine Settings")
    
    # Securely ask for API Key
    api_key = st.text_input("Paste YouTube API Key", type="password", help="Get this from Google Cloud Console")
    
    st.divider()
    
    max_channels = st.slider("Channels to Scan", 3, 10, 5, help="How many top channels to analyze?")
    videos_per_channel = st.slider("Videos per Channel", 10, 50, 30, help="How far back to look?")
    outlier_multiplier = st.slider("Outlier Threshold", 1.5, 5.0, 3.0, step=0.5, help="3.0 means 3x higher views than average")
    
    st.info("💡 **Tip:** Higher 'Channels to Scan' takes longer. 5 is a good balance.")

# --- HELPER FUNCTIONS ---

def get_channel_basics(youtube, channel_id):
    """Fetches basic channel info and the Uploads Playlist ID."""
    try:
        request = youtube.channels().list(
            part="contentDetails,snippet,statistics",
            id=channel_id
        )
        response = request.execute()
        if not response['items']: return None
        item = response['items'][0]
        return {
            'id': channel_id,
            'title': item['snippet']['title'],
            'uploads': item['contentDetails']['relatedPlaylists']['uploads'],
            'subs': int(item['statistics']['subscriberCount']) if not item['statistics']['hiddenSubscriberCount'] else 0
        }
    except:
        return None

def get_videos(youtube, playlist_id, limit):
    """Fetches video statistics from the uploads playlist."""
    videos = []
    try:
        # 1. Get Video IDs
        req = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=limit
        )
        res = req.execute()
        vid_ids = [item['contentDetails']['videoId'] for item in res['items']]
        
        if not vid_ids: return []

        # 2. Get Video Stats (Views)
        stats_req = youtube.videos().list(
            part="statistics,snippet",
            id=','.join(vid_ids)
        )
        stats_res = stats_req.execute()
        
        for item in stats_res['items']:
            videos.append({
                'title': item['snippet']['title'],
                'published': item['snippet']['publishedAt'],
                'views': int(item['statistics'].get('viewCount', 0)),
                'channel': item['snippet']['channelTitle'],
                'url': f"https://www.youtube.com/watch?v={item['id']}"
            })
    except:
        pass
    return videos

def process_channel_logic(api_key, channel_id, v_limit, threshold_mult):
    """
    The 'Worker' function. 
    Connects to YouTube, gets data, calculates math, returns ONLY outliers.
    """
    try:
        yt = build('youtube', 'v3', developerKey=api_key)
        
        # 1. Get Channel Info
        info = get_channel_basics(yt, channel_id)
        if not info: return []
        
        # 2. Get Videos
        videos = get_videos(yt, info['uploads'], v_limit)
        if not videos: return []
        
        # 3. Calculate Math
        df = pd.DataFrame(videos)
        median_views = df['views'].median()
        if median_views == 0: median_views = 1
        
        threshold = median_views * threshold_mult
        
        # 4. Filter Outliers
        outliers = df[df['views'] > threshold].copy()
        
        if not outliers.empty:
            outliers['performance'] = outliers['views'] / median_views
            return outliers.to_dict('records')
            
    except Exception:
        return [] # Return empty if channel fails (keeps engine running)
        
    return []

# --- MAIN APP INTERFACE ---

st.title("⚡ YouTube Market Intelligence Engine")
st.write("Enter a topic. The engine will find the top channels, analyze their history, and identify the **Outlier Videos** (High Demand Topics).")

topic_input = st.text_input("Enter a Topic", placeholder="e.g. Sustainable Living, Notion Setup, AI News")
run_btn = st.button("🚀 Scan Market")

if run_btn:
    if not api_key:
        st.error("⚠️ Please paste your API Key in the sidebar to start.")
    elif not topic_input:
        st.warning("Please enter a topic.")
    else:
        # Create a status container
        status = st.status("Starting Engine...", expanded=True)
        
        try:
            yt = build('youtube', 'v3', developerKey=api_key)
            
            # Step 1: Find Channels
            status.write(f"📡 Identifying top {max_channels} channels for '{topic_input}'...")
            search_req = yt.search().list(
                part="snippet", 
                q=topic_input, 
                type="channel", 
                maxResults=max_channels, 
                order="relevance"
            )
            search_res = search_req.execute()
            channel_ids = [item['snippet']['channelId'] for item in search_res['items']]
            
            if not channel_ids:
                status.update(label="No channels found.", state="error")
                st.stop()
                
            # Step 2: Parallel Analysis
            status.write("⚡ Scanning video performance data (Parallel Processing)...")
            all_outliers = []
            
            # This creates a 'pool' of workers to check multiple channels at once
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [
                    executor.submit(process_channel_logic, api_key, cid, videos_per_channel, outlier_multiplier) 
                    for cid in channel_ids
                ]
                
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        all_outliers.extend(result)
            
            status.update(label="Analysis Complete!", state="complete", expanded=False)
            
            # Step 3: Show Results
            if all_outliers:
                final_df = pd.DataFrame(all_outliers).sort_values('performance', ascending=False)
                
                st.divider()
                
                # Top Level Metrics
                m1, m2, m3 = st.columns(3)
                m1.metric("Channels Analyzed", len(channel_ids))
                m2.metric("Outliers Found", len(final_df))
                m3.metric("Top Multiplier", f"{final_df['performance'].max():.1f}x")
                
                # Scatter Plot
                st.subheader(f"💎 The '{topic_input}' Gold Mine")
                fig = px.scatter(
                    final_df,
                    x="published",
                    y="performance",
                    size="views",
                    color="channel",
                    hover_data=["title", "views"],
                    title="Outlier Intensity (Higher = More Viral)",
                    labels={"performance": "Viral Multiplier (x Average)", "published": "Date"}
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Detailed Table
                st.subheader("📋 Top Opportunities List")
                st.dataframe(
                    final_df[['title', 'channel', 'views', 'performance', 'url']],
                    column_config={
                        "url": st.column_config.LinkColumn("Watch Video"),
                        "performance": st.column_config.NumberColumn("Multiplier", format="%.1fx"),
                        "views": st.column_config.NumberColumn("Views", format="%d")
                    },
                    hide_index=True
                )
            else:
                st.warning("No outliers found. Try lowering the threshold or checking a broader topic.")
                
        except Exception as e:
            st.error(f"Error: {str(e)}")