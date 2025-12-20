import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
    .stButton>button { border-radius: 5px; height: 3em; font-weight: bold; }
    .metric-card { background-color: #f0f2f6; border-left: 5px solid #ff4b4b; padding: 15px; border-radius: 5px; }
    
    /* Verdict Colors */
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
    
    st.info("💡 **Tip:** Use 'Niche Battle' to compare two ideas!")

# --- HELPER FUNCTIONS ---

def assign_competition_level(subs):
    """Classifies competition based on subscriber count."""
    if subs < 100000: return "Very Low (<100k)"
    if subs < 1000000: return "Medium (<1M)"
    return "High (>1M)"

def assign_verdict(row):
    """Automatically classifies the opportunity type."""
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
    """
    NEW STRICTER FORMULA (0-100)
    1. Opportunity Ratio (50%): What % of the outliers are 'Low Competition'?
    2. Viral Intensity (50%): How high is the average multiplier?
    """
    if df.empty: return 0
    
    # 1. Opportunity Ratio (Max 50 pts)
    # We want niches where Small Channels (Low Comp) are winning.
    low_comp_count = len(df[df['Competition'].str.contains("Very Low")])
    total_outliers = len(df)
    opportunity_ratio = low_comp_count / total_outliers
    score_opportunity = opportunity_ratio * 50
    
    # 2. Viral Intensity (Max 50 pts)
    # We cap the multiplier at 10x. If avg is 10x, you get full 50pts.
    avg_mult = df['performance'].mean()
    score_viral = min(50, (avg_mult / 10) * 50)
    
    # Total Score
    final_score = int(score_opportunity + score_viral)
    return max(0, min(100, final_score))

# --- VISUAL HELPERS ---

def create_tag_chart(tags_list, topic):
    """Generates a horizontal bar chart for top tags."""
    # Filter out the topic itself
    clean_tags = [t for t in tags_list if topic.lower() not in t]
    
    if not clean_tags: return None
    
    counts = Counter(clean_tags).most_common(10)
    df_tags = pd.DataFrame(counts, columns=['Tag', 'Frequency'])
    df_tags = df_tags.sort_values('Frequency', ascending=True)
    
    fig = px.bar(
        df_tags, x='Frequency', y='Tag', orientation='h',
        text='Frequency', title="🕸️ Top Related Keywords (Hidden Tags)",
        color='Frequency', color_continuous_scale='Viridis'
    )
    fig.update_layout(yaxis={'title': ''}, xaxis={'title': 'Mentions'}, showlegend=False)
    return fig

# --- YOUTUBE API LOGIC ---

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

def run_market_scan(topic, api_key, max_ch, vid_limit, mult, days, region_code):
    """Reusable function: Scans a topic and returns the DataFrame + Score + Stats."""
    try:
        yt = build('youtube', 'v3', developerKey=api_key)
        
        # 1. Search (With Region Filter)
        search_args = {
            'part': "snippet",
            'q': topic,
            'type': "channel",
            'maxResults': max_ch,
            'order': "relevance",
            'relevanceLanguage': "en"
        }
        if region_code:
            search_args['regionCode'] = region_code

        search_req = yt.search().list(**search_args)
        search_res = search_req.execute()
        channel_ids = [item['snippet']['channelId'] for item in search_res['items']]
        
        if not channel_ids: return None, "No channels found."
        
        # 2. Parallel Scan
        all_outliers = []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(process_channel_logic, api_key, cid, vid_limit, mult, days) 
                for cid in channel_ids
            ]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res: all_outliers.extend(res)
        
        if not all_outliers: return None, "No outliers found."
        
        # 3. Process Data
        df = pd.DataFrame(all_outliers)
        df['Competition'] = df['subs'].apply(assign_competition_level)
        
        # Filter News Spam
        mask_news = (df['Competition'] == "High (>1M)") & (df['views'] < 50000)
        df = df[~mask_news]
        
        if df.empty: return None, "No quality outliers found (Spam Filter Active)."

        # Sort & Diversity
        df = df.sort_values('performance', ascending=False)
        df = df.groupby('channel').head(2).sort_values('performance', ascending=False)
        
        # Calculate Scores
        df['Verdict'] = df.apply(assign_verdict, axis=1)
        score = calculate_niche_score(df, len(channel_ids))
        
        return {
            "df": df,
            "score": score,
            "channels": len(channel_ids),
            "outliers": len(df),
            "top_mult": df['performance'].max() if not df.empty else 0
        }, None

    except Exception as e:
        return None, str(e)

# --- MAIN APP INTERFACE ---

st.title("⚡ YouTube Market Intelligence Engine")

# Create Tabs for different modes
tab1, tab2 = st.tabs(["🔭 Deep Dive (Single)", "⚔️ Niche Battle (Compare)"])

# ==========================================
# TAB 1: VISUAL DASHBOARD (SINGLE)
# ==========================================
with tab1:
    st.write("Deep dive into a single topic to find specific video ideas.")
    col_search, col_btn = st.columns([4, 1])
    topic_input = col_search.text_input("Enter Topic", placeholder="e.g. Ambient, Focus Music", label_visibility="collapsed", key="search_single")
    run_btn = col_btn.button("🚀 Scan", type="primary", key="btn_single", use_container_width=True)

    if run_btn:
        if not api_key: st.error("⚠️ Please paste API Key in sidebar.")
        elif not topic_input: st.warning("Enter a topic.")
        else:
            # Use spinner instead of status (Prevents the 'Click to Open' issue)
            with st.spinner("Analyzing Market Data..."):
                data, err = run_market_scan(topic_input, api_key, max_channels, videos_per_channel, outlier_multiplier, days_back, selected_region_code)
                
            if err:
                st.error(err)
            else:
                final_df = data['df']
                niche_score = data['score']

                # --- 1. DASHBOARD HEADER ---
                st.divider()
                
                # Dynamic Score Color
                score_color = "#ef553b" # Red
                if niche_score > 50: score_color = "#ffa15a" # Orange
                if niche_score > 75: score_color = "#00cc96" # Green
                
                c_score, c_info = st.columns([2, 3])
                
                with c_score:
                    st.markdown(f"""
                    <div style="text-align: center; border: 2px solid {score_color}; padding: 20px; border-radius: 10px;">
                        <h2 style="margin:0; font-size: 3em; color: {score_color};">{niche_score}</h2>
                        <p style="margin:0; font-weight: bold; opacity: 0.8;">Niche Opportunity Score</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with c_info:
                    st.markdown("### 🔍 Quick Summary")
                    if niche_score > 75:
                        st.success("✅ **Excellent Market.** Lots of small channels are going viral here. Easy to enter.")
                    elif niche_score > 50:
                        st.warning("⚠️ **Moderate.** Some small channels are winning, but big channels dominate. Needs a unique angle.")
                    else:
                        st.error("❌ **Saturated.** Mostly dominated by giants. Very hard to get views.")
                    
                    m1, m2 = st.columns(2)
                    m1.metric("Viral Outliers", data['outliers'], help="Number of videos performing significantly above average.")
                    m2.metric("Top Multiplier", f"{data['top_mult']:.1f}x", help="The highest performing video got this many times more views than normal.")

                # --- 2. STRATEGY CARDS (Legend) ---
                st.write("") # Spacer
                st.subheader("🗺️ The Strategy Legend")
                st.info("Use this guide to understand the table below:")
                
                st.markdown(f"""
                <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px;">
                    <div style="flex: 1; background-color: #e6f4ea; padding: 15px; border-radius: 10px; border-left: 5px solid #00cc96;">
                        <h4 style="margin:0; color: #008000;">💎 Gold Mine</h4>
                        <small><b>Definition:</b> Small channel (<100k subs) + Massive Views.<br><b>Action:</b> Copy this Topic immediately.</small>
                    </div>
                    <div style="flex: 1; background-color: #e8f0fe; padding: 15px; border-radius: 10px; border-left: 5px solid #636efa;">
                        <h4 style="margin:0; color: #1967d2;">🌟 Rising Star</h4>
                        <small><b>Definition:</b> Small channel + Consistent Views.<br><b>Action:</b> Study their thumbnails.</small>
                    </div>
                    <div style="flex: 1; background-color: #fce8e6; padding: 15px; border-radius: 10px; border-left: 5px solid #ef553b;">
                        <h4 style="margin:0; color: #c5221f;">🦈 Shark Tank</h4>
                        <small><b>Definition:</b> Giant channel (>1M subs) winning.<br><b>Action:</b> Avoid / Do not copy.</small>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # --- 3. SCATTER PLOT ---
                st.divider()
                st.subheader(f"💎 Opportunity Map: '{topic_input}'")
                fig = px.scatter(
                    final_df, x="published", y="performance", size="views", color="Verdict",
                    hover_data=["title", "channel"],
                    labels={"performance": "Viral Multiplier", "published": "Upload Date"},
                    color_discrete_map={"💎 Gold Mine": "#00CC96", "🌟 Rising Star": "#636EFA", "✅ Good Bet": "#AB63FA", "🌊 Mainstream Wave": "#FFA15A", "🦈 Shark Tank (Avoid)": "#EF553B"}
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # --- 4. ACTIONABLE TABLE (With Tooltips) ---
                st.subheader("📋 Ranked Video List")
                st.dataframe(
                    final_df[['Verdict', 'title', 'channel', 'views', 'performance', 'url']], 
                    hide_index=True, 
                    column_config={
                        "url": st.column_config.LinkColumn("Link"), 
                        "title": st.column_config.TextColumn("Video Title", width="large"),
                        "views": st.column_config.NumberColumn("Views", help="Total views this video has received."),
                        "performance": st.column_config.ProgressColumn(
                            "Viral Power", 
                            format="%.1f x", 
                            min_value=0, 
                            max_value=10,
                            help="How many times BETTER this video did compared to the channel's average. 10x means it got 1000% more views than normal. Higher is better."
                        ),
                        "Verdict": st.column_config.TextColumn(
                            "Verdict", 
                            width="medium",
                            help="Gold Mine = Best Opportunity. Shark Tank = High Competition."
                        ),
                    }
                )

# ==========================================
# TAB 2: VISUAL BATTLE (COMPARE)
# ==========================================
with tab2:
    st.write("Compare two topics head-to-head to see which is the better opportunity.")
    col1, col2 = st.columns(2)
    t1 = col1.text_input("Topic A", placeholder="Stoicism", key="topic_a")
    t2 = col2.text_input("Topic B", placeholder="Ambient", key="topic_b")
    
    fight_btn = st.button("⚔️ Start Battle", key="btn_fight", type="primary")
    
    if fight_btn:
        if not api_key or not t1 or not t2:
            st.error("Please enter API Key and both topics.")
        else:
            status = st.status("Running Niche Battle...", expanded=True)
            res1, err1 = run_market_scan(t1, api_key, max_channels, videos_per_channel, outlier_multiplier, days_back, selected_region_code)
            res2, err2 = run_market_scan(t2, api_key, max_channels, videos_per_channel, outlier_multiplier, days_back, selected_region_code)
            status.update(label="Battle Complete!", state="complete", expanded=False)
            
            if err1 or err2:
                st.error(f"Error: {err1 or err2}")
            else:
                # --- WINNER BANNER ---
                if res1['score'] > res2['score']:
                    winner = t1
                    diff = res1['score'] - res2['score']
                else:
                    winner = t2
                    diff = res2['score'] - res1['score']
                
                st.markdown(f"""
                <div style="background: linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%); padding: 20px; border-radius: 10px; color: white; text-align: center; margin-bottom: 30px;">
                    <h2 style="margin:0;">🏆 Winner: {winner}</h2>
                    <p style="margin:0; opacity: 0.9;">Better Opportunity Score (+{diff} points)</p>
                </div>
                """, unsafe_allow_html=True)
                
                # --- COMPARISON CHARTS ---
                c_chart, c_metrics = st.columns([2, 1])
                
                with c_chart:
                    # Side by Side Bar Chart
                    comp_data = pd.DataFrame({
                        'Topic': [t1, t1, t1, t2, t2, t2],
                        'Metric': ['Score', 'Outliers', 'Viral Max', 'Score', 'Outliers', 'Viral Max'],
                        'Value': [res1['score'], res1['outliers'], res1['top_mult'], res2['score'], res2['outliers'], res2['top_mult']]
                    })
                    fig_comp = px.bar(comp_data, x="Metric", y="Value", color="Topic", barmode="group", title="Head-to-Head Stats", text_auto=True)
                    st.plotly_chart(fig_comp, use_container_width=True)
                
                with c_metrics:
                    st.subheader("Key Stats")
                    st.metric(f"{t1} Score", res1['score'])
                    st.metric(f"{t2} Score", res2['score'])
                    st.caption(f"viral max x10 for scale")
