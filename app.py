import streamlit as st
import pandas as pd
import plotly.express as px
from googleapiclient.discovery import build
import concurrent.futures
from datetime import datetime, timedelta
from collections import Counter

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
    
    /* Tag Spider Chip Styling */
    .tag-container {
        background-color: #ffffff;
        color: #333333;
        padding: 8px 12px;
        border-radius: 20px;
        text-align: center;
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
        font-size: 0.9em;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }

    /* NEW: Verdict Colors */
    .verdict-gold { color: #008000; font-weight: bold; }
    .verdict-star { color: #DAA520; font-weight: bold; }
    .verdict-shark { color: #B22222; font-weight: bold; }
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
    
    # --- Date Filter Slider ---
    st.divider()
    days_back = st.slider("Look Back Period (Days)", 30, 365, 90, help="90 for Trends, 365 for Evergreen")
    
    st.info("💡 **Tip:** Use the 'Niche Health' score below to validate your idea!")

# --- HELPER FUNCTIONS ---

def assign_competition_level(subs):
    """Classifies competition based on subscriber count."""
    if subs < 100000: return "Very Low (<100k)"
    if subs < 1000000: return "Medium (<1M)"
    return "High (>1M)"

def assign_verdict(row):
    """NEW: Automatically classifies the opportunity type."""
    comp = row['Competition']
    perf = row['performance']
    
    if "Very Low" in comp:
        if perf > 5.0: return "💎 Gold Mine"
        return "🌟 Rising Star"
    elif "Medium" in comp:
        if perf > 5.0: return "✅ Good Bet"
        return "⚖️ Neutral"
    else: # High Comp
        if perf > 10.0: return "🌊 Mainstream Wave"
        return "🦈 Shark Tank (Avoid)"

def calculate_niche_score(df, total_channels):
    """NEW: Calculates a 0-100 score for the niche."""
    # Base score
    score = 50 
    
    # 1. Reward Low Competition Outliers
    low_comp_count = len(df[df['Competition'].str.contains("Very Low")])
    score += (low_comp_count * 10)
    
    # 2. Reward High Viral Intensity
    avg_multiplier = df['performance'].mean()
    score += (avg_multiplier * 2)
    
    # 3. Penalize High Competition Saturation
    high_comp_count = len(df[df['Competition'].str.contains("High")])
    score -= (high_comp_count * 5)
    
    # Cap between 0 and 100
    return max(0, min(100, int(score)))

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

def get_videos(youtube, playlist_id, limit, days_back):
    """Fetches video statistics from the uploads playlist, filtering by date and getting tags."""
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

        # 2. Get Video Stats (Views & Tags)
        stats_req = youtube.videos().list(
            part="statistics,snippet",
            id=','.join(vid_ids)
        )
        stats_res = stats_req.execute()
        
        for item in stats_res['items']:
            # --- Date Filtering Logic ---
            pub_date_str = item['snippet']['publishedAt']
            pub_date = datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ")
            
            # Skip if older than days_back
            if (datetime.now() - pub_date).days > days_back:
                continue

            videos.append({
                'title': item['snippet']['title'],
                'published': item['snippet']['publishedAt'],
                'views': int(item['statistics'].get('viewCount', 0)),
                'channel': item['snippet']['channelTitle'],
                'tags': item['snippet'].get('tags', []),
                'url': f"https://www.youtube.com/watch?v={item['id']}"
            })
    except:
        pass
    return videos

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
            # Ensure tags are carried over
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
            status.write(f"⚡ Scanning video performance data (Last {days_back} Days)...")
            all_outliers = []
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = [
                    executor.submit(process_channel_logic, api_key, cid, videos_per_channel, outlier_multiplier, days_back) 
                    for cid in channel_ids
                ]
                
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        all_outliers.extend(result)
            
            status.update(label="Analysis Complete!", state="complete", expanded=False)
            
            # Step 3: Show Results (DECISION ENGINE UPDATE)
            if all_outliers:
                final_df = pd.DataFrame(all_outliers)
                final_df['Competition'] = final_df['subs'].apply(assign_competition_level)
                
                # Filter News Spam
                mask_news_spam = (final_df['Competition'] == "High (>1M)") & (final_df['views'] < 50000)
                final_df = final_df[~mask_news_spam]
                
                # Sort & Diversity Filter
                final_df = final_df.sort_values('performance', ascending=False)
                final_df = final_df.groupby('channel').head(2).sort_values('performance', ascending=False)
                
                if final_df.empty:
                    st.warning(f"No quality outliers found in the last {days_back} days.")
                else:
                    # --- NEW: APPLY DECISION LOGIC ---
                    final_df['Verdict'] = final_df.apply(assign_verdict, axis=1)
                    niche_score = calculate_niche_score(final_df, len(channel_ids))
                    
                    # --- NEW: DISPLAY NICHE HEALTH ---
                    st.divider()
                    st.subheader("📊 Niche Health Report")
                    
                    c1, c2, c3 = st.columns(3)
                    
                    # Score Color Logic
                    score_color = "red"
                    if niche_score > 50: score_color = "orange"
                    if niche_score > 75: score_color = "green"
                    
                    c1.markdown(f"### Niche Score: <span style='color:{score_color}'>{niche_score}/100</span>", unsafe_allow_html=True)
                    if niche_score > 75:
                        c1.caption("✅ Excellent opportunity. Lots of Green Dots.")
                    elif niche_score > 50:
                        c1.caption("⚠️ Good potential, specific angles needed.")
                    else:
                        c1.caption("❌ High competition / Low viral interest.")

                    c2.metric("Channels Scanned", len(channel_ids))
                    c3.metric("Outliers Found", len(final_df))
                    
                    # --- TAG SPIDER (Keep existing) ---
                    st.divider()
                    st.subheader("🕸️ Discovered Niches (Common Tags)")
                    
                    all_tags = [tag.lower() for row in final_df['tags'] for tag in row]
                    filtered_tags = [t for t in all_tags if topic_input.lower() not in t]
                    
                    if filtered_tags:
                        common_tags = Counter(filtered_tags).most_common(12)
                        cols = st.columns(4)
                        for i, (tag, count) in enumerate(common_tags):
                            cols[i % 4].markdown(f"<div class='tag-container'>{tag} ({count})</div>", unsafe_allow_html=True)
                    else:
                        st.caption("No unique tags found.")

                    st.divider()
                    
                    # --- VISUALIZATION (Updated Colors) ---
                    st.subheader(f"💎 The '{topic_input}' Gold Mine")
                    st.caption("Look for Gold Mines (Low Comp, High Viral).")
                    
                    fig = px.scatter(
                        final_df,
                        x="published",
                        y="performance",
                        size="views",
                        color="Verdict", # <--- UPDATED: Colors by Verdict
                        hover_data=["title", "views", "channel"],
                        title="Opportunity Landscape",
                        labels={"performance": "Viral Multiplier", "published": "Date"},
                        color_discrete_map={
                            "💎 Gold Mine": "#00CC96",      # Green
                            "🌟 Rising Star": "#636EFA",    # Blue
                            "✅ Good Bet": "#AB63FA",       # Purple
                            "🌊 Mainstream Wave": "#FFA15A", # Orange
                            "🦈 Shark Tank (Avoid)": "#EF553B" # Red
                        }
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # --- TABLE (Updated Columns) ---
                    st.subheader("📋 Ranked Opportunities")
                    
                    # Move Verdict to front of table
                    cols_to_show = ['Verdict', 'title', 'channel', 'views', 'performance', 'url']
                    
                    st.dataframe(
                        final_df[cols_to_show],
                        column_config={
                            "url": st.column_config.LinkColumn("Watch"),
                            "performance": st.column_config.NumberColumn("Multiplier", format="%.1fx"),
                            "views": st.column_config.NumberColumn("Views", format="%d"),
                            "Verdict": st.column_config.TextColumn("Verdict")
                        },
                        hide_index=True
                    )
            else:
                st.warning("No outliers found. Try lowering the threshold or checking a broader topic.")
                
        except Exception as e:
            st.error(f"Error: {str(e)}")
