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
# Import custom components
from config import COORD_EMAIL
from utils import send_automated_email
from api_service import fetch_all_courses, fetch_course_metadata, is_api_ready
from data_processing import calculate_student_metrics, process_logs_and_merge, calculate_risk_scores
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

log_file = st.sidebar.file_uploader("📂 Upload Moodle Activity Logs (CSV)", type=["csv"])

# Log Analysis Window
log_window_days = st.sidebar.slider("Log Analysis Window (Days)", 7, 180, 30, help="Only analyze activity from the last X days found in the log file.")
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

st.sidebar.metric("Target Final Mark", f"{total_target}/100")



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

# ================== 6. LOG INTEGRATION ==================
if not users_raw:
    st.info("👋 **Welcome! Please select a Course in the sidebar to get started.**")
    total_dwell_hours = 0.0
else:
    df, total_dwell_hours = process_logs_and_merge(df, log_file, users_raw, window_days=log_window_days)

# ================== 7. RISK SCORING ==================
if df.empty:
    st.warning("No student data available for risk calculation.")
else:
    df = calculate_risk_scores(df, weight_config)


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
        early_warn_df = df[df['Risk_Category'].isin(['🔴 Critical','🟡 Warning'])][['Name','Assignments_Gap','Quizzes_Gap','Risk_Category']]
        if not early_warn_df.empty:
            st.dataframe(early_warn_df, width="stretch")
        else:
            st.success("All students are on track! ✅")
    else:
        st.info("No data available.")

    m1, m2, m3, m4 = st.columns(4)
    if not df.empty:
        m1.metric("Avg Final Mark", f"{df['Final_Mark'].mean():.2f}")
        if 'Status' in df.columns:
            m2.metric("Inactive Students", len(df[df['Status']=="Inactive"]))
        else:
             m2.metric("Inactive Students", 0)
        m3.metric("Total Dwell Hours", f"{total_dwell_hours:.2f}h")
        if 'Risk_Score' in df.columns:
            m4.metric("Avg Risk Score", f"{df['Risk_Score'].mean():.1f}%")
        else:
             m4.metric("Avg Risk Score", "0.0%")

# ---------- Tab 2: Risk Scatter ----------
with tab2:
    st.markdown("### Risk Scatter: Click a dot to see student details")
    color_map = {'🔴 Critical':'red','🟡 Warning':'yellow','🟢 Safe':'green'}
    if not df.empty and 'Risk_Category' in df.columns:
        fig = px.scatter(
            df,
            x='Engagement_Score',
            y='Final_Mark',
            size='Dwell_Hours',
            color='Risk_Category',
            color_discrete_map=color_map,
            hover_name='Name',
            hover_data=['Assignments_Gap', 'Quizzes_Gap', 'Risk_Score'],
            labels={
                'Engagement_Score':'Engagement',
                'Final_Mark':'Grade',
                'Assignments_Gap': 'Missed Assignments',
                'Quizzes_Gap': 'Missed Quizzes',
                'Risk_Score': 'Risk Score'
            },
            height=600
        )
        fig.update_yaxes(range=[0,100], title_text="Final Mark (0-100)")
        fig.update_xaxes(title_text="Engagement Score")
        st.plotly_chart(fig, width='stretch')
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
                col1.metric("Final Mark", f"{s['Final_Mark']}/100")
                col2.metric("Engagement", f"{s['Engagement_Score']}%")
                col3.metric("Dwell Hours", f"{s['Dwell_Hours']:.2f}h")
                col4.metric("Total Clicks", f"{int(s['Clicks'])}")
                col5.metric("Last Active", f"{int(s['Days_Since_Last'])} days ago")
                st.markdown(f"**Risk Score:** {s['Risk_Score']:.1f} ({s['Risk_Category']})")

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
        # Risk Score threshold slider
        col1, col2 = st.columns([1, 1])
        with col1:
            t_val = st.slider("Risk Score Threshold:", 0, 100, 50)
        with col2:
            cat_filter = st.multiselect("Include Categories:", ['🔴 Critical', '🟡 Warning', '🟢 Safe'], default=['🔴 Critical', '🟡 Warning'])
        
        # Filter: Score >= Threshold OR Category in Selected
        preview_targets = df[
            (df['Risk_Score'] >= t_val) | 
            (df['Risk_Category'].isin(cat_filter))
        ][['Name', 'Email', 'Risk_Score', 'Risk_Category', 'Assignments_Gap', 'Quizzes_Gap', 'Clicks', 'Days_Since_Last', 'Status']].copy()

        st.markdown(f"### Target List (Score ≥ {t_val} OR Categories: {', '.join(cat_filter)})")
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
        st.info("💡 **Available Placeholders:** `{Name}`, `{Risk_Score}`, `{Assignments_Gap}`, `{Quizzes_Gap}`, `{Clicks}`, `{Days_Since_Last}`, `{Status}`")
        
        default_template = """Hi {Name},

You are at risk of falling behind. Please see your current details:

- Risk Score: {Risk_Score}
- Assignments Gap: {Assignments_Gap}
- Quizzes Gap: {Quizzes_Gap}
- Total Clicks: {Clicks}
- Last Active: {Days_Since_Last} days ago
- Status: {Status}

Please take immediate action to improve your performance."""

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
                            Risk_Score=r['Risk_Score'],
                            Assignments_Gap=r['Assignments_Gap'],
                            Quizzes_Gap=r['Quizzes_Gap'],
                            Clicks=int(r['Clicks']),
                            Days_Since_Last=int(r['Days_Since_Last']),
                            Status=r['Status']
                        )
                    except KeyError as e:
                        st.error(f"❌ Placeholder error: {e}. Please check your template.")
                        break

                    # Preview email
                    with st.expander(f"Preview Email to {r['Name']}", expanded=False):
                        st.code(body)

                    # Send email
                    success = send_automated_email(r['Email'], "Risk Alert", body)
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
    st.write("""
    - **Unified Engagement Score (60%)**: A composite score of activity and progress:
        - **Activity (50% of engagement)**: Combined Clicks and Dwell Time (page activity).
        - **Assessment Completion (50% of engagement)**: Percentage of **overdue** items submitted.
    - **Performance Component (40%)**: Quality of marks (percentage of available points achieved).
    - **Risk Score** = 100 - (0.6 * Unified Engagement + 0.4 * Performance)
    - **Risk Categories**:
        - 🔴 Critical: Risk Score > 75 OR 3+ missed **overdue** quizzes OR 2+ missed **overdue** assignments.
        - 🟡 Warning: Risk Score 50-75 OR 2+ missed **overdue** quizzes OR 1+ missed **overdue** assignment.
        - 🟢 Safe: Risk Score < 50.
    """)

# ---------- Tab 6: Detailed Results ----------
with tab6:
    st.markdown("### Student Detailed Performance (Percentage Marks)")

    if df.empty:
        st.info("No data.")
    else:
        detailed_list = []
        for _, u in df.iterrows():
            row = {
                "User_ID": u['User_ID'],
                "Name": u['Name'],
                "Email": u['Email'],
                "Final_Mark (%)": u['Final_Mark'],
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
                row[f"{cfg['name']} (%)"] = round(perc, 1)

            detailed_list.append(row)

        detailed_df = pd.DataFrame(detailed_list)

        # Display the table
        st.dataframe(detailed_df, width="stretch")

        # CSV download
        csv = detailed_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Detailed Results CSV",
            data=csv,
            file_name="student_detailed_results_percentage.csv",
            mime="text/csv"
        )


st.divider()
st.caption(f"Sync: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Multi-factor Risk Dashboard | Produced by Sunny")
