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
from data_processing import calculate_student_metrics, process_logs_and_merge, calculate_risk_scores, get_log_date_range
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
else:
    st.sidebar.success("Moodle Connection Established")
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
if course_id:
    if st.sidebar.button("🔄 Sync New Data from Moodle"):
        clear_course_cache(course_id)
        st.success("Cache cleared! Fetching fresh data...")
        st.rerun()

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

with st.sidebar.expander("Moodle Metadata Debug"):
    for k, v in weight_config.items():
        st.write(f"**{v['name']}**")
        st.write(f"- Moodle ID: {v['id']}")
        st.write(f"- Moodle Max Grade: {v.get('grademax', 'N/A')}")
        st.write(f"- Sidebar Weight: {v['weight']}")
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
st.divider()

# Calculate metrics using data_processing module
student_results, teacher_results = calculate_student_metrics(users_raw, weight_config, course_id, submission_data, quiz_attempts_raw)

# --- INJECT REDIS DRAFTS INTO MAIN PIPELINE ---
from redis_client import get_redis, PREFIX_DRAFT
redis_client = get_redis()
draft_key = f"{PREFIX_DRAFT}{course_id}"
all_drafts = redis_client.get_json(draft_key) or {} # user_id -> {item_key: val}

if all_drafts:
    for row in student_results:
        u_id_str = str(row['User_ID'])
        if u_id_str in all_drafts:
            u_drafts = all_drafts[u_id_str]
            # Override both raw and weighted points for each item
            for item_k, new_raw in u_drafts.items():
                row[f"raw_{item_k}"] = float(new_raw)
                # Re-calculate points based on weight (simplified weight logic)
                # Note: This is an approximation; ideally calculate_student_metrics handles it.
                # But for immediate UI feedback, overriding the total is most important.
            
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
        early_warn_df = df[df['Risk_Category'].isin(['Critical','Warning'])][['Name', 'Class', 'Group', 'Score', 'Assignments_Gap','Quizzes_Gap','Risk_Category']]
        if not early_warn_df.empty:
            st.dataframe(early_warn_df, use_container_width=True, hide_index=True)
        else:
            st.success("All students are on track.")
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

# ---------- View: Risk Scatter ----------
elif choice == "Risk Scatter":

    st.markdown("### Risk Scatter: Click a dot to see student details")
    color_map = {'Critical':'red','Warning':'yellow','Safe':'green'}
    if not df.empty and 'Risk_Category' in df.columns:
        # Prepare data for plotting
        plot_df = df.copy()
        # 1. Normalize Performance to % (to handle courses with different total marks)
        target = max(total_target, 1)
        plot_df['Performance_Perc'] = (plot_df['Final_Mark'] / target * 100).round(2)
        
        # 2. Ensure every dot is visible by adding a minimum size constant
        plot_df['Plot_Size'] = plot_df['Dwell_Hours'] + 5 

        fig = px.scatter(
            plot_df,
            x='Engagement_Score',
            y='Performance_Perc',
            size='Plot_Size',
            color='Risk_Category',
            color_discrete_map=color_map,
            hover_name='Name',
            hover_data={
                'Class': True,
                'Group': True,
                'Performance_Perc': False,
                'Score': True,
                'Assignments_Gap': True,
                'Quizzes_Gap': True,
                'Risk_Score': True,
                'Engagement_Score': ':.2f',
                'Plot_Size': False 
            },
            labels={
                'Engagement_Score':'Engagement (%)',
                'Performance_Perc':'Performance (%)',
                'Score': 'Current Score',
                'Assignments_Gap': 'Missed Assignments',
                'Quizzes_Gap': 'Missed Quizzes',
                'Risk_Score': 'Risk Score'
            },
            height=600
        )
        # Ensure axis ranges are -5 to 105 so nothing is cut off at the edges
        fig.update_yaxes(range=[-5, 105], title_text="Performance % (Weighted Mark / Total)")
        fig.update_xaxes(range=[-5, 105], title_text="Engagement Score (%)")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for scatter plot.")

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
    render_detailed_results(df, total_target, weight_config, course_id, group_mapping=group_mapping, metadata=metadata)


st.divider()
st.caption(f"Sync: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Data Source: Moodle API | Unified Risk Analytics")
