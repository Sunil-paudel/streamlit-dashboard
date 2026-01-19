# ========================================================================================
# File: apilog2.py
# Description: Main Entry Point for the Moodle Student Dropout Prevention Dashboard.
# Author: Sunny (Refactored by Antigravity)
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
import pandas as pd
import numpy as np
import os
from datetime import datetime

# Import custom components
from config import COORD_EMAIL
from utils import send_automated_email
from api_service import fetch_all_courses, fetch_course_metadata, is_api_ready
from data_processing import calculate_student_metrics, process_logs_and_merge, calculate_risk_scores, get_log_date_range
import plotly.express as px

st.set_page_config(page_title="Student Risk Prevention Hub", layout="wide")

# ================== 3. API STATUS CHECK ==================
api_ok, api_msg = is_api_ready()
if not api_ok:
    st.sidebar.error(f"🚫 **Moodle Connection Issue**\n\n{api_msg}")
    st.info("👋 **Welcome! Please check your Moodle configuration in the .env file.**")
    st.stop()
else:
    # Add a Refresh Button to clear cache
    if st.sidebar.button("🔄 Refresh Course Data"):
        st.cache_data.clear()
        st.rerun()

# ================== 4. SIDEBAR COURSE & WEIGHT CONFIG ==================
st.sidebar.header("🎓 Course Setup")
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

log_file = st.sidebar.file_uploader("📂 Upload Moodle Activity Logs (CSV)", type=["csv"])

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
st.sidebar.subheader("⚖️ Assessment Weight Setup")

users_raw, quizzes_raw, assigns_raw, submission_data, quiz_attempts_raw = fetch_course_metadata(course_id)
weight_config = {}
total_target = 0

with st.sidebar.expander("Set Assessment Weights", expanded=True):
    for q in quizzes_raw:
        w = st.slider(f"Quiz: {q['name'][:25]}", 0.0, 20.0, 5.0, key=f"q_{q['id']}")
        if w > 0:
            # Debug: Show quiz fields
            if 'coursemodule' not in q:
                st.warning(f"DEBUG: Quiz '{q['name']}' is missing 'coursemodule' field. Available fields: {list(q.keys())}")
            
            weight_config[f"quiz_{q['id']}"] = {
                'id': int(q['id']), 
                'cmid': q.get('coursemodule'),
                'weight': w, 
                'type': 'quiz', 
                'name': q['name'],
                'duedate': q.get('timeclose', 0),
                'visible': q.get('visible', 1)
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
                'visible': a.get('visible', 1)
            }
            total_target += w

st.sidebar.metric("Target Final Mark", f"{total_target:.2f} pts")

st.sidebar.markdown("---")
st.sidebar.subheader("🔬 Risk Formula Setup")
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



# ==========================================
# 5. CALCULATION ENGINE
# ==========================================
st.title("🎯 Moodle Analytics Hub")

# Calculate metrics using data_processing module
student_results, teacher_results = calculate_student_metrics(users_raw, weight_config, course_id, submission_data, quiz_attempts_raw)

if not student_results:
    df = pd.DataFrame(columns=['User_ID', 'Name', 'Email', 'Final_Mark', 'Assignments_Gap', 'Quizzes_Gap'])
else:
    df = pd.DataFrame(student_results)
    # Add a display column for Marks / Total
    df['Score'] = df['Final_Mark'].apply(lambda x: f"{x} / {total_target}")

# ================== 6. LOG INTEGRATION ==================
if not users_raw:
    st.info("👋 **Welcome! Please select a Course in the sidebar to get started.**")
    total_dwell_hours = 0.0
else:
    df, total_dwell_hours = process_logs_and_merge(df, log_file, users_raw, start_date=start_date, end_date=end_date)

# ================== 7. RISK SCORING ==================
if df.empty:
    st.warning("No student data available for risk calculation.")
else:
    df = calculate_risk_scores(df, weight_config, formula_config=formula_config)


# ================== 8. COURSE TEAM ==================
st.markdown("### 🧑‍🏫 Course Team")
# teacher_results is already filtered in calculate_student_metrics
if teacher_results:
    t_cols = st.columns(min(len(teacher_results),5))
    for idx, t in enumerate(teacher_results):
        with t_cols[idx%5]: st.info(f"**{t['Name']}**\n\n{t.get('Email','N/A')}")

# ================== 9. MAIN TABS ==================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview", "📉 Risk Scatter", "📋 Student Details",
    "✉️ Outreach", "📚 Methodology", "📑 Detailed Results"
])

# ---------- Tab 1: Overview ----------
with tab1:
    st.markdown("### 🛑 Early Prevention Alerts")
    if not df.empty and 'Risk_Category' in df.columns:
        early_warn_df = df[df['Risk_Category'].isin(['🔴 Critical','🟡 Warning'])][['Name', 'Score', 'Assignments_Gap','Quizzes_Gap','Risk_Category']]
        if not early_warn_df.empty:
            st.dataframe(early_warn_df, width="stretch")
        else:
            st.success("All students are on track! ✅")
    else:
        st.info("No data available.")

    m1, m2, m3, m4 = st.columns(4)
    if not df.empty:
        m1.metric("Avg Final Mark", f"{df['Final_Mark'].mean():.2f} / {total_target:.2f}")
        if 'Status' in df.columns:
            m2.metric("Inactive Students", len(df[df['Status']=="Inactive"]))
        else:
             m2.metric("Inactive Students", 0)
        m3.metric(f"Total Dwell Hours ({log_window_days}d)", f"{total_dwell_hours:.2f}h")
        if 'Risk_Score' in df.columns:
            m4.metric("Avg Risk Score", f"{df['Risk_Score'].mean():.2f}%")
        else:
             m4.metric("Avg Risk Score", "0.00%")

# ---------- Tab 2: Risk Scatter ----------
with tab2:
    st.markdown("### Risk Scatter: Click a dot to see student details")
    color_map = {'🔴 Critical':'red','🟡 Warning':'yellow','🟢 Safe':'green'}
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

# ---------- Tab 3: Student Details ----------
with tab3:
    if not df.empty:
        lookup = st.selectbox("Select Student", df['Name'].sort_values().unique())
        
        if lookup:
            subset = df[df['Name']==lookup]
            if not subset.empty:
                s = subset.iloc[0]
                st.markdown(f"### Student: {s['Name']}")
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Final Mark", f"{s['Final_Mark']:.2f} / {total_target:.2f}")
                col2.metric("Engagement", f"{s['Engagement_Score']:.2f}%")
                col3.metric("Clicks / Week", f"{s.get('Clicks_Per_Week', 0.0):.2f}")
                col4.metric(f"Total Clicks ({log_window_days}d)", f"{int(s.get('Clicks', 0))}")
                col5.metric(f"Dwell Hours ({log_window_days}d)", f"{s.get('Dwell_Hours', 0.0):.2f}h")
                st.markdown(f"**Last Active:** {int(s['Days_Since_Last'])} days ago | **Risk Score:** {s['Risk_Score']:.2f} ({s['Risk_Category']})")

                breakdown = []
                for k,v in weight_config.items():
                    pts = s.get(f"pts_{k}", 0)
                    is_overdue = s.get(f"overdue_{k}", False)
                    is_inprogress = s.get(f"inprogress_{k}", False)
                    is_viewed = s.get(f"viewed_{k}", False)
                    
                    if pts > 0:
                        status_icon = "✅"
                    elif is_overdue:
                        status_icon = "⚠️"
                    elif is_inprogress or is_viewed:
                        status_icon = "🔄"
                    else:
                        status_icon = "⏳"

                    breakdown.append({
                        "Assessment": v['name'],
                        "Due Date": s.get(f"due_{k}", "N/A"),
                        "Raw": s.get(f"raw_{k}", 0),
                        "Points": pts,
                        "Max": s.get(f"max_{k}", v['weight']),
                        "Timing": s.get(f"timing_{k}", "N/A"),
                        "Status": status_icon
                    })
                st.table(pd.DataFrame(breakdown))
            else:
                st.warning("Student data not found.")
        else:
            st.info("No students available to display.")
    else:
         st.info("No data available.")


# ---------- Tab 4: Outreach ----------
with tab4:
    st.markdown("### ✉️ Student Outreach & Email Alerts")

    if not df.empty and 'Risk_Score' in df.columns:
        # -------- Filter Controls --------
        st.markdown("#### 🎯 Filter Controls")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("**Risk-Based Filtering**")
            t_val = st.slider("Risk Score Threshold:", 0, 100, 50)
            cat_filter = st.multiselect("Include Categories:", ['🔴 Critical', '🟡 Warning', '🟢 Safe'], default=['🔴 Critical', '🟡 Warning'])
        
        with col2:
            st.markdown("**Assessment-Based Filtering**")
            # Build list of assessment names
            item_options = [f"{cfg['name']}" for key, cfg in weight_config.items()]
            selected_items = st.multiselect(
                "Filter by Specific Items:",
                options=item_options,
                help="Select specific quizzes or assignments to target students who scored below the threshold."
            )
            score_threshold = st.slider(
                "Score Threshold (%):",
                min_value=0,
                max_value=100,
                value=40,
                step=10,
                help="Students scoring below this % in ANY selected item will be included."
            )
        
        # Logic toggle
        narrow_by_risk = st.checkbox(
            "Narrow by Activity/Risk? (AND logic)",
            value=False,
            help="If checked, students must match BOTH risk filters AND item filters. If unchecked, students matching EITHER will be included."
        )
        
        # -------- Apply Filters --------
        # Risk-based mask
        risk_mask = (df['Risk_Score'] >= t_val) | (df['Risk_Category'].isin(cat_filter))
        
        # Item-based mask
        item_mask = pd.Series([False] * len(df), index=df.index)
        if selected_items:
            for idx, row in df.iterrows():
                for key, cfg in weight_config.items():
                    if cfg['name'] in selected_items:
                        # Check if student scored below threshold
                        raw = row.get(f"raw_{key}", 0)
                        max_pts = row.get(f"max_{key}", cfg['weight']) or cfg['weight']
                        perc = (raw / max_pts * 100) if max_pts > 0 else 0
                        if perc < score_threshold:
                            item_mask[idx] = True
                            break
        
        # Combine masks based on logic
        if selected_items and narrow_by_risk:
            # AND logic: must match both
            final_mask = risk_mask & item_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)} **AND** Scoring < {score_threshold:.0f}% in: {', '.join(selected_items)}"
        elif selected_items:
            # OR logic: match either
            final_mask = risk_mask | item_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)} **OR** Scoring < {score_threshold:.0f}% in: {', '.join(selected_items)}"
        else:
            # Only risk-based
            final_mask = risk_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)}"
        
        # Build the base columns
        base_cols = ['Name', 'Email', 'Risk_Score', 'Risk_Category', 'Assignments_Gap', 'Quizzes_Gap', 'Clicks', 'Days_Since_Last', 'Status']
        preview_targets = df[final_mask][base_cols].copy()
        
        # Add individual assessment scores as percentage columns
        for key, cfg in weight_config.items():
            col_name = f"{cfg['name']} (%)"
            preview_targets[col_name] = df[final_mask].apply(
                lambda row: round((row.get(f"raw_{key}", 0) / (row.get(f"max_{key}", cfg['weight']) or cfg['weight']) * 100) if (row.get(f"max_{key}", cfg['weight']) or cfg['weight']) > 0 else 0, 2),
                axis=1
            )

        st.markdown(f"### Target List ({filter_desc})")
        if not preview_targets.empty:
            # Add selection column
            preview_targets.insert(0, "Select", True)
            
            # Interactive editor
            edited_df = st.data_editor(
                preview_targets,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Email?",
                        help="Select students to send emails to",
                        default=True,
                    )
                },
                disabled=["Name", "Email", "Risk_Score", "Assignments_Gap", "Quizzes_Gap", "Clicks", "Days_Since_Last", "Status"],
                hide_index=True,
                width="stretch"
            )
            
            # Filter for selected students
            final_targets = edited_df[edited_df['Select']]
        else:
            st.info("No students exceed the selected Risk Score threshold.")
            final_targets = pd.DataFrame()

        # -------- Custom Email Template --------
        st.markdown("---")
        st.subheader("📝 Customize Email Template")
        st.info("💡 **Available Placeholders:** `{Name}`, `{Risk_Category}`, `{Assignments_Gap}`, `{Quizzes_Gap}`, `{Days_Since_Last}`")
        
        default_template = """Hi {Name},

We’re reaching out to check in and offer support, as our learning system indicates that you may benefit from reviewing your current course engagement.

Here’s a brief overview of your current progress:

• Risk category: {Risk_Category}
• Pending assignments: {Assignments_Gap}
• Pending quizzes: {Quizzes_Gap}
• Course activity: Below class average
• Last active: {Days_Since_Last} days ago

These indicators help us identify students who may need additional support. If you’ve been facing any challenges—academic, technical, or personal—please know that help is available.

We encourage you to log in, review your upcoming tasks, and reach out to your course coordinator or student support services if you need assistance. Taking early action can make a meaningful difference.

Kind regards,
Student Support Team"""

        email_template = st.text_area("Email Message Body", value=default_template, height=300)

        # -------- Student Emails --------
        if st.button(f"📨 Email Selected Students ({len(final_targets)})"):
            if final_targets.empty:
                st.warning("No students selected.")
            else:
                sent_count = 0
                for _, r in final_targets.iterrows():
                    # Format individual email using placeholders
                    try:
                        body = email_template.format(
                            Name=r['Name'],
                            Risk_Category=r['Risk_Category'],
                            Assignments_Gap=r['Assignments_Gap'],
                            Quizzes_Gap=r['Quizzes_Gap'],
                            Days_Since_Last=int(r['Days_Since_Last'])
                        )
                    except KeyError as e:
                        st.error(f"❌ Placeholder error: {e}. Please check your template.")
                        break

                    # Preview email
                    with st.expander(f"Preview Email to {r['Name']}", expanded=False):
                        st.code(body)

                    # Send email
                    success = send_automated_email(r['Email'], "A quick check-in about your course progress", body)
                    if success:
                        st.success(f"✅ Email sent to {r['Name']}")
                        sent_count += 1
                    else:
                        st.error(f"❌ Failed to send email to {r['Name']}")

                st.info(f"Total emails successfully sent: {sent_count}/{len(final_targets)}")

        # -------- Coordinator Summary --------
        st.markdown("---")
        st.subheader("Coordinator Summary")
        # coord_email_input is already defined at top of sidebar logic

        if st.button("📋 Send Coordinator Summary"):
            body = f"""Coordinator Alert: {len(preview_targets)} at-risk students.

Details:
{preview_targets.to_string(index=False)}
"""
            # Preview coordinator email
            st.markdown("#### Preview Coordinator Email")
            st.code(body)

            # Send email
            # Use coord_email_input from sidebar
            success = send_automated_email(coord_email_input, "Course Risk Summary", body)
            if success:
                st.success("✅ Coordinator notified")
            else:
                st.error("❌ Failed to send coordinator email")
    else:
        st.info("No student data available for outreach.")


# ---------- Tab 5: Methodology ----------
with tab5:
    st.markdown("### Methodology")
    st.write(f"""
    - **Unified Engagement Score ({int(eng_ow*100)}%)**: A composite score of activity and progress:
        - **Activity ({int(act_w*100)}% of engagement)**: Combined Clicks and Dwell Time (page activity).
        - **Assessment Completion ({int(comp_w*100)}% of engagement)**: Percentage of **overdue** items submitted.
    - **Performance Component ({int(perf_ow*100)}%)**: Quality of marks (percentage of available points achieved).
    - **Risk Score** = 100 - ({eng_ow} * Unified Engagement + {round(perf_ow, 2)} * Performance)
    - **Risk Categories**:
        - 🔴 Critical: Risk Score > 75 OR 3+ missed **overdue** quizzes OR 2+ missed **overdue** assignments.
        - 🟡 Warning: Risk Score 50-75 OR 2+ missed **overdue** quizzes OR 1+ missed **overdue** assignment.
        - 🟢 Safe: Risk Score < 50.
    """)

# ---------- Tab 6: Detailed Results ----------
with tab6:
    st.markdown("### Student Detailed Performance (Editable)")
    st.info("💡 **Edit assessment scores below and click 'Push to Moodle' to sync changes.**")

    if df.empty:
        st.info("No data.")
    else:
        # Initialize session state for tracking changes
        if 'grade_changes' not in st.session_state:
            st.session_state.grade_changes = {}
        
        detailed_list = []
        for _, u in df.iterrows():
            row = {
                "User_ID": u['User_ID'],
                "Name": u['Name'],
                "Email": u['Email'],
                "Score": f"{u['Final_Mark']:.2f} / {total_target:.2f}",
                "Clicks": int(u.get('Clicks', 0)),
                "Dwell_Hours": round(u.get('Dwell_Hours', 0), 2),
                "Days_Since_Last": int(u.get('Days_Since_Last', 0)),
                "Status": u.get('Status', 'N/A'),
            }

            # Add individual assessment as percentage
            for k, cfg in weight_config.items():
                r = u.get(f"raw_{k}", 0.0)
                # Use the 'max' column computed during metrics calculation
                m = u.get(f"max_{k}", cfg['weight']) or cfg['weight']
                perc = (r / m * 100) if m > 0 else 0
                row[f"{cfg['name']} (%)"] = round(perc, 2)
            
            # Add adjustment reason column
            row["Adjustment Reason"] = ""

            detailed_list.append(row)

        detailed_df = pd.DataFrame(detailed_list)
        
        # Make assessment columns editable
        editable_cols = [col for col in detailed_df.columns if col.endswith(" (%)")]
        disabled_cols = [col for col in detailed_df.columns if col not in editable_cols and col != "Adjustment Reason"]

        # Display editable table
        edited_df = st.data_editor(
            detailed_df,
            disabled=disabled_cols,
            hide_index=True,
            use_container_width=True,
            key="detailed_results_editor"
        )

        # Detect changes
        changes_detected = []
        for idx, (orig_row, edit_row) in enumerate(zip(detailed_df.iterrows(), edited_df.iterrows())):
            orig_data = orig_row[1]
            edit_data = edit_row[1]
            
            for col in editable_cols:
                if abs(orig_data[col] - edit_data[col]) > 0.01:  # Tolerance for floating point
                    # Extract assessment key from column name
                    assessment_name = col.replace(" (%)", "")
                    # Find the corresponding key in weight_config
                    item_key = None
                    for k, cfg in weight_config.items():
                        if cfg['name'] == assessment_name:
                            item_key = k
                            break
                    
                    if item_key:
                        item_cmid = weight_config[item_key].get('cmid')
                        # Debug: Show what we're capturing
                        if weight_config[item_key]['type'] == 'quiz':
                            st.write(f"DEBUG: Quiz {weight_config[item_key]['name']} - ID: {weight_config[item_key]['id']}, CMID: {item_cmid}")
                        
                        changes_detected.append({
                            'user_id': edit_data['User_ID'],
                            'name': edit_data['Name'],
                            'item_key': item_key,
                            'item_name': assessment_name,
                            'item_type': weight_config[item_key]['type'],
                            'item_id': weight_config[item_key]['id'],
                            'item_cmid': item_cmid,
                            'old_perc': orig_data[col],
                            'new_perc': edit_data[col],
                            'max_points': weight_config[item_key]['weight'],
                            'reason': edit_data.get('Adjustment Reason', '')
                        })

        # Review Pending Changes
        if changes_detected:
            with st.expander(f"📋 Review Pending Changes ({len(changes_detected)} modifications)", expanded=True):
                for change in changes_detected:
                    st.markdown(f"""
                    **{change['name']}** - {change['item_name']}:
                    - Old: {change['old_perc']:.2f}% → New: {change['new_perc']:.2f}%
                    - Reason: {change['reason'] if change['reason'] else '_No reason provided_'}
                    """)
            
            # Push to Moodle button
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.warning("⚠️ **Warning**: This will update grades in your Moodle Gradebook. Make sure you have reviewed all changes above.")
            with col2:
                if st.button("🔄 Push to Moodle", type="primary"):
                    from api_service import sync_grade_to_moodle
                    
                    success_count = 0
                    fail_count = 0
                    
                    with st.spinner("Syncing grades to Moodle..."):
                        for change in changes_detected:
                            # Convert percentage back to raw score
                            new_raw = (change['new_perc'] / 100) * change['max_points']
                            
                            # Sync both assignments and quizzes
                            success, message = sync_grade_to_moodle(
                                course_id=course_id,
                                user_id=change['user_id'],
                                item_id=change['item_id'],
                                item_type=change['item_type'],
                                grade_value=new_raw,
                                item_cmid=change.get('item_cmid')
                            )
                            
                            if success:
                                st.success(f"✅ {change['name']} - {change['item_name']}: {message}")
                                success_count += 1
                            else:
                                st.error(f"❌ {change['name']} - {change['item_name']}: {message}")
                                fail_count += 1
                    
                    st.info(f"Sync complete: {success_count} successful, {fail_count} failed/skipped")
                    
                    # Clear cache to refresh data
                    if success_count > 0:
                        st.cache_data.clear()
                        st.info("💡 Refresh the page to see updated grades from Moodle.")

        # CSV download
        st.markdown("---")
        csv = edited_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Detailed Results CSV (with adjustments)",
            data=csv,
            file_name="student_detailed_results_edited.csv",
            mime="text/csv"
        )


st.divider()
st.caption(f"Sync: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Multi-factor Risk Dashboard | Produced by Sunny")
