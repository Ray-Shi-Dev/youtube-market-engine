import streamlit as st
import pandas as pd
import plotly.express as px
from googleapiclient.discovery import build
import concurrent.futures
from datetime import datetime, timedelta # --- NEW: Required for date math

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

    # --- Region Selector ---
    st.subheader("🌍 Region Filter")
    region_options = {
        "Worldwide (English Focus)": None,
        "United States": "US",
        "United Kingdom": "GB",
        "Canada": "CA",
        "Australia": "AU",
        "India": "IN"
    }
    selected_region_label = st.selectbox("Target Region", options=list(region_options.keys()), index=0)
    selected_region_code = region_options[selected_region_label]
    
    st.divider()
    
    max_channels = st.slider("Channels to Scan", 3, 10, 5, help="How many top channels to analyze?")
    videos_per_channel = st.slider("Videos per Channel", 10, 50, 30, help="How far back to look?")
    outlier_multiplier = st.slider("Outlier Threshold", 1.5, 5.0, 3.0, step=0.5, help="3.0 means 3x higher views than average")
    
    # --- NEW: Date Filter Slider ---
    st.divider()
    days_back = st.slider("Look Back Period (Days)", 30, 365, 90, help="90 for Trends, 365 for Evergreen")
    
    st.info("💡 **Tip:** Higher 'Channels to Scan' takes longer. 5 is a good balance.")

# --- HELPER FUNCTIONS ---

def assign_competition_level(subs):
    """Classifies competition based on subscriber count."""
    if subs < 100000: return "Very Low (<100k)"
    if subs < 1000000: return "Medium (<1M)"
    return "High (>1M)"

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

# --- UPDATED: Accepts days_back ---
def get_videos(youtube, playlist_id, limit, days_back):
    """Fetches video statistics from the uploads playlist, filtering by date."""
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
            # --- NEW: Date Filtering Logic ---
            pub_date_str = item['snippet']['publishedAt']
            # Parse format: 2023-10-27T10:00:00Z
            pub_date = datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ")
            
            # Skip if older than days_back
            if (datetime.now() - pub_date).days > days_back:
                continue
            # ---------------------------------

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

# --- UPDATED: Accepts days_back ---
def process_channel_logic(api_key, channel_id, v_limit, threshold_mult, days_back):
    """
    The 'Worker' function. 
    Connects to YouTube, gets data, calculates math, returns ONLY outliers.
    """
    try:
        yt = build('youtube', 'v3', developerKey=api_key)
        
        # 1. Get Channel Info
        info = get_channel_basics(yt, channel_id)
        if not info: return []
        
        # 2. Get Videos (Pass days_back)
        videos = get_videos(yt, info['uploads'], v_limit, days_back)
        if not videos: return []
        
        # 3. Calculate Math
        df = pd.DataFrame(videos)
        
        # Attach Subscriber count to DataFrame
        df['subs'] = info['subs']
        
        median_views = df['views'].median()
        if median_views == 0: median_views = 1
        
        threshold = median_views * threshold_mult
        
        # 4. Filter Outliers
        outliers = df[df['views'] > threshold].copy()
        
        if not outliers.empty:
            outliers['performance'] = outliers['views'] / median_views
            return outliers.to_dict('records')
            
    except Exception:
        return [] 
        
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
            region_msg = f"in {selected_region_label}" if selected_region_code else "Worldwide"
            status.write(f"📡 Identifying top {max_channels} English channels for '{topic_input}' ({region_msg})...")
            
            search_args = {
                'part': "snippet",
                'q': topic_input,
                'type': "channel",
                'maxResults': max_channels,
                'order': "relevance",
                'relevanceLanguage': "en" 
            }
            if selected_region_code:
                search_args['regionCode'] = selected_region_code

            search_req = yt.search().list(**search_args)
            search_res = search_req.execute()
            channel_ids = [item['snippet']['channelId'] for item in search_res['items']]
            
            if not channel_ids:
                status.update(label="No channels found.", state="error")
                st.stop()
                
            # Step 2: Parallel Analysis
            status.write("⚡ Scanning video performance data (Parallel Processing)...")
            all_outliers = []
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # --- UPDATED: Pass days_back to the worker ---
                futures = [
                    executor.submit(process_channel_logic, api_key, cid, videos_per_channel, outlier_multiplier, days_back) 
                    for cid in channel_ids
                ]
                
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        all_outliers.extend(result)
            
            status.update(label="Analysis Complete!", state="complete", expanded=False)
            
            # Step 3: Show Results
            if all_outliers:
                final_df = pd.DataFrame(all_outliers)
                
                # Apply Competition Logic
                final_df['Competition'] = final_df['subs'].apply(assign_competition_level)
                
                # --- FILTERING LOGIC ---
                
                # 1. Remove "News Spam": If High Competition, require > 50k views
                mask_news_spam = (final_df['Competition'] == "High (>1M)") & (final_df['views'] < 50000)
                final_df = final_df[~mask_news_spam]
                
                # 2. Sort by Performance
                final_df = final_df.sort_values('performance', ascending=False)
                
                # 3. Apply "Diversity Filter": Keep only top 2 videos per channel
                final_df = final_df.groupby('channel').head(2).sort_values('performance', ascending=False)
                
                # -------------------------------
                
                if final_df.empty:
                    st.warning(f"No outliers found in the last {days_back} days. Try increasing the Look Back Period or lowering the threshold.")
                else:
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
                        color="Competition",
                        hover_data=["title", "views", "channel"],
                        title=f"Outlier Intensity ({days_back} Days Look Back)",
                        labels={"performance": "Viral Multiplier (x Average)", "published": "Date"},
                        color_discrete_map={
                            "Very Low (<100k)": "#00CC96",
                            "Medium (<1M)": "#636EFA",
                            "High (>1M)": "#EF553B"
                        }
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Detailed Table
                    st.subheader("📋 Top Opportunities List")
                    st.dataframe(
                        final_df[['title', 'channel', 'Competition', 'views', 'performance', 'url']],
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
