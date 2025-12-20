import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from googleapiclient.discovery import build
import concurrent.futures
from datetime import datetime, timedelta
from collections import Counter
import math

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
    NEW 'VOLUME + INTENSITY' FORMULA (0-100)
    Corrects the 'Small Niche' bias. 
    A niche must have both VOLUME (Depth) and INTENSITY (Virality) to score high.
    """
    if df.empty: return 0
    
    # 1. Market Depth (0-50 pts)
    # How many outliers did we find? (Reward Volume)
    # < 5 outliers = Niche too small (Low Score)
    # > 20 outliers = High Demand (Max Score)
    outliers_count = len(df)
    score_depth = min(50, outliers_count * 2.5) 
    
    # 2. Viral Intensity (0-50 pts)
    # How explosive are the videos? (Reward Quality)
    # Avg 2x multiplier = 10 pts
    # Avg 10x multiplier = 50 pts
    avg_mult = df['performance'].median()
    score_viral = 0
    if avg_mult > 1:
        score_viral = min(50, avg_mult * 5)
    
    # 3. Shark Penalty (Multiplier)
    # If giants dominate (>50%), reduce total score by 30%
    sharks = len(df[df['Verdict'].str.contains("Shark", na=False)])
    penalty_factor = 1.0
    if outliers_count > 0 and (sharks / outliers_count > 0.5):
        penalty_factor = 0.7
    
    final_score = int((score_depth + score_viral) * penalty_factor)
    return max(0, min(100, final_score))

# --- VISUAL HELPERS ---

def create_gauge_chart(score):
    """Generates a professional speedometer style gauge for the score."""
    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = score,
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': "Niche Opportunity Score", 'font': {'size': 24}},
        gauge = {
            'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': "darkblue"},
            'bgcolor': "white",
            'borderwidth': 2,
            'bordercolor': "gray",
            'steps': [
                {'range': [0, 50], 'color': '#ffebec'},  # Red Zone
                {'range': [50, 75], 'color': '#fff4e5'}, # Orange Zone
                {'range': [75, 100], 'color': '#e6f4ea'} # Green Zone
            ],
            'threshold': {
                'line': {'color': "red", 'width': 4},
                'thickness': 0.75,
                'value': score
            }
        }
    ))
    fig.update_layout(height=300, margin=dict(l=20, r=20, t=50, b=20))
    return fig

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
    """
    Reusable function: Scans a topic and returns the DataFrame + Score + Stats.
    UPDATED: Now returns Median Multiplier and Shark Count for better Battle insights.
    """
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
            "top_mult": df['performance'].max() if not df.empty else 0,
            "median_mult": df['performance'].median() if not df.empty else 0, # <--- NEW
            "shark_count": len(df[df['Verdict'].str.contains("Shark", na=False)]) # <--- NEW
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
            with st.spinner("Analyzing Market Data..."):
                data, err = run_market_scan(topic_input, api_key, max_channels, videos_per_channel, outlier_multiplier, days_back, selected_region_code)
                
            if err:
                st.error(err)
            else:
                final_df = data['df']
                niche_score = data['score']

                # --- 1. DASHBOARD HEADER (GAUGE & METRICS) ---
                st.divider()
                col_gauge, col_stats = st.columns([1, 2])
                
                with col_gauge:
                    # Professional Gauge Chart
                    st.plotly_chart(create_gauge_chart(niche_score), use_container_width=True)
                
                with col_stats:
                    st.subheader("📊 Market Health Check")
                    
                    # Display metrics row
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Channels Scanned", data['channels'])
                    m2.metric("Outliers Found", data['outliers'])
                    m3.metric("Top Multiplier", f"{data['top_mult']:.1f}x")

                # --- 2. STRATEGY LEGEND (NATIVE STREAMLIT FIX) ---
                st.divider()
                st.subheader("🗺️ The Strategy Legend")
                
                # Using Native Columns + Containers (100% Reliability)
                leg1, leg2, leg3, leg4 = st.columns(4)
                
                with leg1:
                    with st.container(border=True):
                        st.markdown("#### 💎 Gold Mine")
                        st.caption("**Small Channel + Huge Views**")
                        st.write("Action: **Copy Topic**")
                
                with leg2:
                    with st.container(border=True):
                        st.markdown("#### 🌟 Rising Star")
                        st.caption("**Consistent Growth**")
                        st.write("Action: **Study Style**")
                
                with leg3:
                    with st.container(border=True):
                        st.markdown("#### 🌊 Mainstream")
                        st.caption("**Big Trend Wave**")
                        st.write("Action: **Be Fast**")
                        
                with leg4:
                    with st.container(border=True):
                        st.markdown("#### 🦈 Shark Tank")
                        st.caption("**Giant Dominance**")
                        st.write("Action: **Avoid**")

                # --- 3. TAG VISUALIZATION ---
                st.divider()
                all_tags = [tag.lower() for row in final_df['tags'] for tag in row]
                tag_chart = create_tag_chart(all_tags, topic_input)
                if tag_chart:
                    st.plotly_chart(tag_chart, use_container_width=True)
                else:
                    st.info("No hidden tags found in the top videos.")

                # --- 4. SCATTER PLOT ---
                st.divider()
                st.subheader(f"💎 Opportunity Map: '{topic_input}'")
                fig = px.scatter(
                    final_df, x="published", y="performance", size="views", color="Verdict",
                    hover_data=["title", "channel"],
                    labels={"performance": "Viral Multiplier", "published": "Upload Date"},
                    color_discrete_map={"💎 Gold Mine": "#00CC96", "🌟 Rising Star": "#636EFA", "✅ Good Bet": "#AB63FA", "🌊 Mainstream Wave": "#FFA15A", "🦈 Shark Tank (Avoid)": "#EF553B"}
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # --- 5. ACTIONABLE TABLE (With Tooltips) ---
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
                    # UPDATED: Side by Side Bar Chart with more metrics (Median & Shark Count)
                    comp_data = pd.DataFrame({
                        'Topic': [
                            t1, t1, t1, t1,
                            t2, t2, t2, t2
                        ],
                        'Metric': [
                            'Score', 'Total Outliers', 'Median Viral (x10)', 'Shark Count',
                            'Score', 'Total Outliers', 'Median Viral (x10)', 'Shark Count'
                        ],
                        'Value': [
                            res1['score'], res1['outliers'], res1['median_mult']*10, res1['shark_count'],
                            res2['score'], res2['outliers'], res2['median_mult']*10, res2['shark_count']
                        ]
                    })
                    
                    fig_comp = px.bar(
                        comp_data, 
                        x="Metric", 
                        y="Value", 
                        color="Topic", 
                        barmode="group", 
                        title="Head-to-Head Stats", 
                        text_auto=True,
                        color_discrete_sequence=["#3b82f6", "#ef553b"] # Blue vs Red
                    )
                    st.plotly_chart(fig_comp, use_container_width=True)
                
                with c_metrics:
                    st.subheader("Key Stats")
                    st.metric(f"{t1} Score", res1['score'])
                    st.metric(f"{t2} Score", res2['score'])
                    
                    st.divider()
                    
                    # INSIGHTS LOGIC
                    # 1. Volume Check
                    if res1['outliers'] > res2['outliers'] * 1.5:
                        st.info(f"**{t1}** is a deeper market (More videos).")
                    elif res2['outliers'] > res1['outliers'] * 1.5:
                        st.info(f"**{t2}** is a deeper market (More videos).")
                        
                    # 2. Saturation Check (The Shark Warning)
                    if res1['shark_count'] > res2['shark_count']:
                        st.warning(f"**{t1}** is more saturated with Giants.")
                    elif res2['shark_count'] > res1['shark_count']:
                        st.warning(f"**{t2}** is more saturated with Giants.")
