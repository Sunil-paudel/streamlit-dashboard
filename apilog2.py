# ========================================================================================
# File: apilog2.py
# Description: Main Entry Point for the Moodle Student Dropout Prevention Dashboard.
# Author: Sunny
# Last Modified: 2026-01-15
#
# Purpose:
#   - Initializes the Streamlit application and configuration.
#   - Sets up the sidebar for Course selection and Assessment Weight configuration.
#   - Orchestrates the data flow by calling services to fetch data, process it,
#     and calculate risk metrics.
#   - Renders the UI tabs (Overview, Risk Scatter, Student Details, Outreach, etc.).
#   - Handles user interactions such as sending emails and downloading reports.
#
# Dependencies:
#   - Streamlit (UI framework)
#   - Plotly Express (Visualizations)
#   - Custom modules: config, utils, api_service, data_processing
# ========================================================================================

import streamlit as st
from components.results import render_detailed_results
from components.details import render_student_details
import pandas as pd
import numpy as np
import os
from datetime import datetime

# Import custom components
from config import COORD_EMAIL
from utils import send_automated_email
from api_service import fetch_all_courses, fetch_course_metadata, is_api_ready, clear_course_cache
from data_processing import calculate_student_metrics, process_logs_and_merge, calculate_risk_scores, get_log_date_range, aggregate_weekly_activity
import plotly.express as px
from components.class_analytics import render_class_analytics
from components.outreach import render_outreach

st.set_page_config(page_title="Student Risk Analytics Dashboard", layout="wide")

# ================== 3. API STATUS CHECK ==================
api_ok, api_msg = is_api_ready()
if not api_ok:
    st.sidebar.error(f"Moodle Connection Issue\n\n{api_msg}")
    st.info("System Configuration Required. Please check Moodle settings in the .env file.")
    st.stop()

# ================== 4. SIDEBAR COURSE & WEIGHT CONFIG ==================
st.sidebar.header("Course Setup")
courses_df = fetch_all_courses()
if not courses_df.empty:
    # Check if 'id' and 'fullname' exist (sometimes API returns errors as list of dicts)
    if 'id' in courses_df.columns and 'fullname' in courses_df.columns:
        # Filter out course ID 1 (Site/Front Page)
        courses_df = courses_df[courses_df['id'] != 1]
        
        courses_df['display'] = courses_df['id'].astype(str) + " - " + courses_df['fullname']
        course_options = courses_df['display'].tolist()
        
        if course_options:
            choice = st.sidebar.selectbox("Select Course", options=course_options)
            course_id = int(choice.split(" - ")[0])
        else:
            st.sidebar.warning("No courses found (excluding Site Home).")
            course_id = st.sidebar.number_input("Enter Course ID", value=1)
    else:
        st.sidebar.error("Could not parse course list. Check API permissions.")
        course_id = st.sidebar.number_input("Enter Course ID", value=1)
else:
    course_id = st.sidebar.number_input("Enter Course ID", value=1)


# Initialize Session State for dynamic log dates
if 'default_start' not in st.session_state:
    st.session_state.default_start = datetime.now().replace(day=1).date()
if 'default_end' not in st.session_state:
    st.session_state.default_end = datetime.now().date()
if 'prev_log_name' not in st.session_state:
    st.session_state.prev_log_name = None
if 'nav_choice' not in st.session_state:
    st.session_state.nav_choice = "Overview"

log_file = st.sidebar.file_uploader("Upload Moodle Activity Logs (CSV)", type=["csv"])

# Detect Log File change and update date defaults
if log_file and log_file.name != st.session_state.prev_log_name:
    min_date, max_date = get_log_date_range(log_file)
    if min_date and max_date:
        st.session_state.default_start = min_date
        st.session_state.default_end = max_date
        st.session_state.prev_log_name = log_file.name
        st.rerun()

# Log Analysis Window (Date Picker)
date_range = st.sidebar.date_input(
    "Log Analysis Period", 
    value=(st.session_state.default_start, st.session_state.default_end),
    help="Select the start and end dates. Defaults to the range found in your uploaded log file."
)

if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date = date_range[0] if date_range else st.session_state.default_start
    end_date = start_date

log_window_days = (end_date - start_date).days + 1
if log_window_days < 1: log_window_days = 1
st.sidebar.markdown("---")
# coord_email_input is defined later in original code, but we can init default here or keep consistent
coord_email_input = st.sidebar.text_input("Coordinator Email", value=COORD_EMAIL)
st.sidebar.markdown("---")
st.sidebar.subheader("Assessment Weight Setup")

metadata = fetch_course_metadata(course_id)
users_raw = metadata['users']
quizzes_raw = metadata['quizzes']
assigns_raw = metadata['assigns']
submission_data = metadata['submissions']
quiz_attempts_raw = metadata['quiz_attempts']
group_mapping = {
    'user_to_groups': metadata['user_to_groups'],
    'group_membership': metadata['group_membership'],
    'groups': metadata['groups'],
    'groupings': metadata['groupings']
}
weight_config = {}
total_target = 0

with st.sidebar.expander("Set Assessment Weights", expanded=True):
    for q in quizzes_raw:
        w = st.slider(f"Quiz: {q['name'][:25]}", 0.0, 20.0, 5.0, key=f"q_{q['id']}")
        if w > 0:
            
            weight_config[f"quiz_{q['id']}"] = {
                'id': int(q['id']), 
                'cmid': q.get('coursemodule'),
                'weight': w, 
                'type': 'quiz', 
                'name': q['name'],
                'duedate': q.get('timeclose', 0),
                'visible': q.get('visible', 1),
                'grademax': float(q.get('grade', 100.0))
            }
            total_target += w
    for a in assigns_raw:
        w = st.slider(f"Assign: {a['name'][:25]}", 0.0, 50.0, 30.0, key=f"a_{a['id']}")
        if w > 0:
            weight_config[f"assign_{a['id']}"] = {
                'id': int(a['id']), 
                'cmid': a.get('cmid'),
                'weight': w, 
                'type': 'assign', 
                'name': a['name'],
                'duedate': a.get('duedate', 0),
                'visible': a.get('visible', 1),
                'grademax': float(a.get('grade', 100.0)),
                'teamsubmission': a.get('teamsubmission', 0),
                'groupingid': a.get('groupingid', 0)
            }
            total_target += w

st.sidebar.metric("Target Final Mark", f"{total_target:.2f} pts")


st.sidebar.subheader("Risk Formula Setup")
with st.sidebar.expander("Customize Weights", expanded=False):
    st.info("Adjust the components that determine student risk.")
    
    st.write("**Engagement Mix**")
    act_w_perc = st.slider("Activity (Clicks/Dwell)", 0, 100, 50, help="Weight of log-based activity in the Engagement Score")
    act_w = act_w_perc / 100.0
    comp_w = 1.0 - act_w
    st.caption(f"Assessment Completion: {int(comp_w*100)}%")
    
    st.markdown("---")
    st.write("**Overall Risk Mix**")
    eng_ow_perc = st.slider("Engagement Weight", 0, 100, 60, help="Weight of Engagement relative to Academic Performance")
    eng_ow = eng_ow_perc / 100.0
    perf_ow = 1.0 - eng_ow
    st.caption(f"Academic Performance: {int(perf_ow*100)}%")
    
    formula_config = {
        'activity_weight': act_w,
        'completion_weight': comp_w,
        'engagement_overall_weight': eng_ow,
        'performance_overall_weight': perf_ow
    }

st.sidebar.markdown("---")
with st.sidebar.expander("Methodology & Logic", expanded=False):
    st.write(f"""
    - **Engagement Mix ({int(eng_ow*100)}%)**:
        - **Activity ({int(act_w*100)}%)**: Combined Clicks and Dwell Time.
        - **Assessments ({int(comp_w*100)}%)**: Overdue items submitted.
    - **Performance ({int(perf_ow*100)}%)**: Quality of marks ACHIEVED.
    - **Risk Score** = 100 - ({eng_ow} * Engagement + {round(perf_ow, 2)} * Performance)
    - **Thresholds**:
        - Critical: Risk > 75 or 3+ missed quizzes
        - Warning: Risk 50-75 or 2+ missed quizzes
    """)



# ==========================================
# 5. CALCULATION ENGINE
# ==========================================
st.title("Student Risk Analytics Dashboard")

# --- Top Navigation Bar ---
nav_options = [
    "Overview", "Risk Scatter", "Student Details",
    "Class Analysis", "Outreach", "Detailed Results"
]
# Use st.radio with horizontal=True for a horizontal navbar
st.session_state.nav_choice = st.radio(
    "Navigation",
    options=nav_options,
    index=nav_options.index(st.session_state.get('nav_choice', "Overview")) if st.session_state.get('nav_choice') in nav_options else 0,
    horizontal=True,
    label_visibility="collapsed"
)

# --- CSS: Reduce Whitespace ---
st.markdown("""
<style>
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 1rem !important;
    }
    .element-container {
        margin-bottom: 0.5rem !important;
    }
    /* Reduce gap between vertical elements */
    [data-testid="stVerticalBlock"] > div {
        gap: 0.5rem !important;
    }
</style>
""", unsafe_allow_html=True)


# Calculate metrics using data_processing module
student_results, teacher_results = calculate_student_metrics(users_raw, weight_config, course_id, submission_data, quiz_attempts_raw)
moodle_baseline_list = [r.copy() for r in student_results] # Keep clean Moodle baseline for Results sync logic

# --- INJECT SESSION STATE DRAFTS INTO MAIN PIPELINE ---
# Initialize drafts_by_course if not exists
if 'drafts_by_course' not in st.session_state:
    st.session_state['drafts_by_course'] = {}

all_drafts = st.session_state['drafts_by_course'].get(course_id, {}) # user_id -> {item_key: val}

if all_drafts:
    for row in student_results:
        u_id_str = str(row['User_ID'])
        if u_id_str in all_drafts:
            u_drafts = all_drafts[u_id_str]
            # Override both raw and weighted points for each item
            for item_k, new_raw in u_drafts.items():
                row[f"raw_{item_k}"] = float(new_raw)
            
            # Recalculate Final_Mark for this row based on injected raw scores
            f_mark = 0.0
            for k, cfg in weight_config.items():
                r_val = float(row.get(f"raw_{k}", 0.0))
                m_val = float(cfg.get('grademax', 100.0) or 100.0)
                w_val = float(cfg.get('weight', 0.0))
                if m_val > 0:
                    f_mark += (r_val / m_val) * w_val
            row['Final_Mark'] = round(f_mark, 2)

if not student_results:
    df = pd.DataFrame(columns=['User_ID', 'Name', 'Email', 'Final_Mark', 'Assignments_Gap', 'Quizzes_Gap'])
else:
    df = pd.DataFrame(student_results)
    # Add a display column for Marks / Total
    df['Score'] = df['Final_Mark'].apply(lambda x: f"{x} / {total_target:.2f}")

# ================== 6. LOG INTEGRATION ==================
if not users_raw:
    st.info("System Ready. Please select a Course in the sidebar to get started.")
    total_dwell_hours = 0.0
else:
    df, total_dwell_hours = process_logs_and_merge(df, log_file, users_raw, start_date=start_date, end_date=end_date)

# ================== 7. RISK SCORING ==================
if df.empty:
    st.warning("No student data available for risk calculation.")
else:
    df = calculate_risk_scores(df, weight_config, formula_config=formula_config)
    
    # --- ADD CLASS & GROUP ("EVERYWHERE") ---
    if metadata:
        # Pre-process group names
        group_id_to_name = {str(g['id']): g['name'] for g in metadata.get('groups', [])}
        group_to_grouping = {}
        if 'groupings' in metadata:
            for gping in metadata['groupings']:
                gn = gping.get('name', 'N/A')
                # Handling moodle response where groups can be nested
                gs = gping.get('groups', [])
                for grp in gs:
                    group_to_grouping[str(grp['id'])] = gn

        def resolve_teams(uid):
            uid_str = str(uid)
            # Use metadata mapping directly
            u_grps = metadata.get('user_to_groups', {}).get(uid_str, [])
            g_names = [group_id_to_name.get(str(gid), "Unknown") for gid in u_grps]
            gp_names = list(set([group_to_grouping.get(str(gid), "No Class") for gid in u_grps]))
            
            final_cls = ", ".join(gp_names) if gp_names else "No Class"
            final_grp = ", ".join(g_names) if g_names else "No Group"
            return final_cls, final_grp

        df['Class'], df['Group'] = zip(*df['User_ID'].map(resolve_teams))
    else:
        df['Class'] = "N/A"
        df['Group'] = "N/A"


# ================== 8. COURSE TEAM ==================
st.markdown("### Course Team")
# teacher_results is already filtered in calculate_student_metrics
if teacher_results:
    t_cols = st.columns(min(len(teacher_results),5))
    for idx, t in enumerate(teacher_results):
        with t_cols[idx%5]: st.info(f"**{t['Name']}**\n\n{t.get('Email','N/A')}")

# ================== 9. MAIN CONTENT (Conditional Rendering) ==================
choice = st.session_state.nav_choice

# ---------- View: Overview ----------
if choice == "Overview":
    st.markdown("### Early Prevention Alerts")

    if not df.empty and 'Risk_Category' in df.columns:
        
        col_alerts, col_chart = st.columns([1.5, 1])
        
        with col_alerts:
            early_warn_df = df[df['Risk_Category'].isin(['Critical','Warning'])][['Name', 'Class', 'Group', 'Score', 'Assignments_Gap','Quizzes_Gap','Risk_Category']]
            if not early_warn_df.empty:
                st.write("**At-Risk Students**")
                st.dataframe(early_warn_df, use_container_width=True, hide_index=True)
            else:
                st.success("🎉 No students currently at risk!")

        with col_chart:
            # Risk Distribution Donut
            risk_counts = df['Risk_Category'].value_counts().reset_index()
            risk_counts.columns = ['Category', 'Count']
            
            color_map = {'Critical': '#ff4b4b', 'Warning': '#ffa421', 'Safe': '#00c0f2'}
            
            fig_donut = px.pie(
                risk_counts, 
                values='Count', 
                names='Category', 
                color='Category',
                color_discrete_map=color_map,
                hole=0.4,
                title="Course Risk Distribution"
            )
            fig_donut.update_traces(textinfo='percent+label', textposition='inside')
            fig_donut.update_layout(showlegend=False, margin=dict(t=30, b=0, l=0, r=0), height=300)
            st.plotly_chart(fig_donut, use_container_width=True)

    else:
        st.info("No data available.")

    m1, m2, m3, m4 = st.columns(4)
    if not df.empty:
        m1.metric("Avg Final Mark", f"{int(df['Final_Mark'].mean())} / {int(total_target)}")
        if 'Status' in df.columns:
            m2.metric("Inactive Students", len(df[df['Status']=="Inactive"]))
        else:
             m2.metric("Inactive Students", 0)
        m3.metric(f"Total Dwell Hours ({log_window_days}d)", f"{total_dwell_hours:.2f}h")
        if 'Risk_Score' in df.columns:
            m4.metric("Avg Risk Score", f"{df['Risk_Score'].mean():.2f}%")
        else:
             m4.metric("Avg Risk Score", "0.00%")
             
    # --- Weekly Activity Trend ---
    st.markdown("### 📈 Weekly Activity Trend")
    weekly_df = aggregate_weekly_activity(log_file, users_raw, start_date=start_date, end_date=end_date)
    
    if not weekly_df.empty:
        fig_trend = px.line(
            weekly_df, 
            x='Week', 
            y='Clicks', 
            title="Class Engagement Over Time (Total Clicks per Week)",
            markers=True
        )
        fig_trend.update_layout(height=350, margin=dict(t=40, b=0, l=0, r=0))
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("Upload logs to see the weekly engagement trend.")

# ---------- View: Risk Scatter ----------
# ---------- View: Risk Radar (Advanced) ----------
elif choice == "Risk Scatter":
    st.markdown("### 📡 Risk Radar: Engagement vs. Performance")
    st.info("This 'Radar' helps you spot students who are **Struggling** (Working hard but failing) vs. **Disengaged** (Not trying).")
    
    if not df.empty and 'Risk_Category' in df.columns:
        # Prepare data
        plot_df = df.copy()
        target = max(total_target, 1)
        plot_df['Performance_Perc'] = (plot_df['Final_Mark'] / target * 100).round(2)
        
        # 1. Cap the dot size so high-dwell students don't block others
        # Base size 8, add log scale or just simple cap. Let's cap at 25.
        plot_df['Plot_Size'] = (plot_df.get('Dwell_Hours', 0) + 8).clip(upper=25)
        
        # --- JITTER LOGIC (To separate overlapping points) ---
        # Increased jitter range to 2.5 for stronger separation of dense clusters
        plot_df['Eng_Jitter'] = plot_df['Engagement_Score'] + np.random.uniform(-2.5, 2.5, len(plot_df))
        plot_df['Perf_Jitter'] = plot_df['Performance_Perc'] + np.random.uniform(-2.5, 2.5, len(plot_df))

        # Define Colors
        color_map = {'Critical': '#ff4b4b', 'Warning': '#ffa421', 'Safe': '#00c0f2'} # Streamlit-like colors
        
        fig = px.scatter(
            plot_df,
            x='Eng_Jitter',    # Use Jittered coordinates
            y='Perf_Jitter',   # Use Jittered coordinates
            size='Plot_Size',
            color='Risk_Category',
            opacity=0.7,       # More transparent to show density
            color_discrete_map=color_map,
            hover_name='Name',
            hover_data={
                'Class': True,
                'Risk_Category': True,
                'Performance_Perc': True,
                'Engagement_Score': ':.1f',
                'Assignments_Gap': True,
                'Quizzes_Gap': True,
                'Plot_Size': False,
                'Eng_Jitter': False, # Hide jitter columns
                'Perf_Jitter': False
            },
            labels={
                'Engagement_Score': 'Engagement (Clicks + Time)',
                'Performance_Perc': 'Performance (Grades %)',
                'Assignments_Gap': 'Missed Asg',
                'Quizzes_Gap': 'Missed Quiz'
            },
            height=650
        )
        
        # Add Border to markers for distinct edges
        fig.update_traces(marker=dict(line=dict(width=1, color='White')))
        
        # Force Hovermode to 'closest' (fixes overlapping tooltips)
        fig.update_layout(hovermode="closest")
        
        # Add Quadrant Lines (The "Radar" Crosshair)
        avg_eng = 50 # Center line at 50% engagement
        pass_mark = 50 # Pass mark at 50%
        
        fig.add_vline(x=avg_eng, line_width=1, line_dash="dash", line_color="grey")
        fig.add_hline(y=pass_mark, line_width=1, line_dash="dash", line_color="grey")

        # Add Quadrant Labels
        fig.add_annotation(x=15, y=15, text="🚨 DANGER ZONE\n(Dropouts)", showarrow=False, font=dict(color="red", size=14))
        fig.add_annotation(x=85, y=15, text="❓ STRUGGLING\n(Needs Help)", showarrow=False, font=dict(color="orange", size=14))
        fig.add_annotation(x=15, y=85, text="🥷 NINJAS\n(Smart/Lucky)", showarrow=False, font=dict(color="blue", size=12))
        fig.add_annotation(x=85, y=85, text="⭐ STARS\n(Ideal)", showarrow=False, font=dict(color="green", size=14))

        # Fix Axes
        fig.update_xaxes(range=[-5, 105], title_font=dict(size=14))
        fig.update_yaxes(range=[-5, 105], title_font=dict(size=14))
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Not enough data to generate the Risk Radar. Need logs and grades.")

# ---------- View: Student Details ----------
elif choice == "Student Details":
    render_student_details(df, total_target, weight_config, log_window_days, group_mapping=group_mapping)

# ---------- View: Class Analysis ----------
elif choice == "Class Analysis":

    render_class_analytics(course_id, users_raw, quizzes_raw, assigns_raw, submission_data, quiz_attempts_raw)

# ---------- View: Outreach ----------
elif choice == "Outreach":
    render_outreach(df, weight_config, coord_email_input, group_mapping=group_mapping)


# ---------- View: Detailed Results ----------
elif choice == "Detailed Results":
    render_detailed_results(df, total_target, weight_config, course_id, group_mapping=group_mapping, metadata=metadata, moodle_baseline=moodle_baseline_list)


st.divider()
st.caption(f"Sync: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Data Source: Moodle API | Unified Risk Analytics")
