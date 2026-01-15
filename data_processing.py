# ========================================================================================
# File: data_processing.py
# Description: Core Business Logic and Data Processing Engine.
#
# Purpose:
#   - Contains the heavy-lifting logic for calculating student performance metrics.
#   - Merges data from multiple sources (API user data, API grades, CSV activity logs).
#   - Implements the Risk Scoring algorithms and Logic for categorization (Critical/Warning/Safe).
#
# Key Functions:
#   - calculate_student_metrics: Iterates users to compute missing assignments/quizzes and final marks.
#   - process_logs_and_merge: Parses CSV logs to compute 'Clicks' and 'Dwell_Hours'.
#   - calculate_risk_scores: Combines Engagement, Assessment, and Performance into a Risk Score.
# ========================================================================================

import pandas as pd
import streamlit as st
from api_service import fetch_user_grades_batch
from utils import calculate_dwell_time

def calculate_student_metrics(users_raw, weight_config, course_id, submission_data=None):
    """
    Iterates through enrolled students and calculates their incomplete assignments,
    quizzes, and current weighted marks, as well as submission timing.
    """
    student_results = []
    teacher_results = []
    staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator']

    # submission_data is {assign_id: {user_id: submission_dict}}

    for user in users_raw:
        user_roles = [r.get('shortname', '').lower() for r in user.get('roles', [])]
        is_staff = any(role in staff_roles for role in user_roles)
        u_info = {'User_ID': user['id'], 'Name': user['fullname'], 'Email': user.get('email', 'N/A')}

        if is_staff:
            teacher_results.append(u_info)
            continue

        row = u_info.copy()
        row['Final_Mark'] = 0.0
        row['Assignments_Gap'] = 0
        row['Quizzes_Gap'] = 0
        grade_items = fetch_user_grades_batch(course_id, user['id'])

        # Track matched grade items to prevent duplicates
        matched_items = set()

        for key, config in weight_config.items():
            r_ob, m_ob, pts_ob = 0.0, 0.0, 0.0
            matched_grade_id = None
            submission_timing = "N/A"
            due_date_str = "N/A"

            # ================= ASSIGNMENTS =================
            if config['type'] == 'assign':
                # Get due date from weight_config if available (added later in apilog2)
                due_timestamp = config.get('duedate', 0)
                if due_timestamp > 0:
                    from datetime import datetime
                    due_date_str = datetime.fromtimestamp(due_timestamp).strftime('%Y-%m-%d')

                # Check submission
                if submission_data and config['id'] in submission_data:
                    user_sub = submission_data[config['id']].get(user['id'])
                    if user_sub and user_sub.get('status') != 'new':
                        # Submission found
                        sub_time = user_sub.get('timemodified', 0)
                        if sub_time > 0 and due_timestamp > 0:
                            diff_days = (due_timestamp - sub_time) / (24 * 3600)
                            if diff_days >= 0:
                                submission_timing = f"{int(diff_days)}d before"
                            else:
                                submission_timing = f"{int(abs(diff_days))}d late"

                for g in grade_items:
                    g_id = g.get('id')
                    if g_id in matched_items:
                        continue

                    g_inst = g.get('iteminstance')
                    g_name = (g.get('itemname') or '').lower().strip()
                    g_module = g.get('itemmodule') or ''

                    if g_module != 'assign':
                        continue

                    # Match by ID
                    if g_inst and int(g_inst) == config['id']:
                        r_ob = float(g.get('graderaw') or 0.0)
                        m_ob = float(g.get('grademax') or 100.0)
                        matched_grade_id = g_id
                        break

                    # Exact name match
                    elif g_name == config['name'].lower().strip():
                        r_ob = float(g.get('graderaw') or 0.0)
                        m_ob = float(g.get('grademax') or 100.0)
                        matched_grade_id = g_id
                        break

                if matched_grade_id:
                    matched_items.add(matched_grade_id)

                if r_ob == 0.0:
                    row['Assignments_Gap'] += 1
                    m_ob = config['weight']

            # ================= QUIZZES =================
            elif config['type'] == 'quiz':
                # Quiz closing time
                due_timestamp = config.get('duedate', 0)
                if due_timestamp > 0:
                    from datetime import datetime
                    due_date_str = datetime.fromtimestamp(due_timestamp).strftime('%Y-%m-%d')
                
                # Note: Quiz submission timing usually requires fetching attempts
                # For now, we mainly focus on assignments as per user request

                for g in grade_items:
                    g_inst = g.get('iteminstance')
                    g_name = (g.get('itemname') or '').lower().strip()
                    g_module = g.get('itemmodule') or ''
                    if g_module != 'quiz':
                        continue

                    # Match by ID
                    if g_inst and int(g_inst) == config['id']:
                        r_ob = float(g.get('graderaw') or 0.0)
                        m_ob = float(g.get('grademax') or config['weight'])
                        matched_grade_id = g.get('id')
                        break

                    # Name match
                    elif g_name == config['name'].lower().strip():
                        r_ob = float(g.get('graderaw') or 0.0)
                        m_ob = float(g.get('grademax') or config['weight'])
                        matched_grade_id = g.get('id')
                        break

                if r_ob == 0.0:
                    row['Quizzes_Gap'] += 1
                    m_ob = config['weight']

            # Weighted points
            pts_ob = (r_ob / m_ob * config['weight']) if m_ob > 0 else 0.0

            # Assign to row
            row[f"raw_{key}"] = r_ob
            row[f"max_{key}"] = m_ob
            row[f"pts_{key}"] = round(pts_ob, 2)
            row[f"timing_{key}"] = submission_timing
            row[f"due_{key}"] = due_date_str
            row['Final_Mark'] += pts_ob

        row['Final_Mark'] = round(row['Final_Mark'], 2)
        row['Early_Warning'] = "⚠️" if (row['Assignments_Gap'] > 0 or row['Quizzes_Gap'] >= 2) else "✅"
        student_results.append(row)
        
    return student_results, teacher_results

def process_logs_and_merge(df, log_file, users_raw, window_days=180):
    """
    Processes the uploaded Moodle log file and merges dwell time / activity stats 
    into the main student DataFrame, restricted by a time window.
    """
    total_dwell_hours = 0.0
    if log_file:
        try:
            logs = pd.read_csv(log_file, on_bad_lines='skip', engine='python', encoding='utf-8')
            time_c = next((c for c in logs.columns if 'time' in c.lower()), None)
            name_c = next((c for c in logs.columns if 'name' in c.lower()), None)
            if time_c and name_c:
                logs[time_c] = pd.to_datetime(logs[time_c], errors='coerce')
                logs = logs.dropna(subset=[time_c])

                # Identify the reference "now" from the logs
                max_log_time = logs[time_c].max()
                
                # Filter logs by the selected window (days)
                if window_days:
                    cutoff = max_log_time - pd.Timedelta(days=window_days)
                    logs = logs[logs[time_c] >= cutoff]

                # Create a set of enrolled student names (exclude staff)
                student_names = [u['fullname'] for u in users_raw
                                 if not any(r['shortname'] in ['teacher','editingteacher','manager','coursecreator']
                                            for r in u.get('roles', []))]
                student_names_lower = [n.lower() for n in student_names]

                # Normalize log names
                logs['Name_LC'] = logs[name_c].str.lower()
                student_logs = logs[logs['Name_LC'].isin(student_names_lower)]

                # Compute dwell hours for only enrolled students
                dwell_stats = student_logs.groupby(name_c).apply(lambda x: calculate_dwell_time(x, time_c)).reset_index()
                dwell_stats.columns = [name_c, 'Dwell_Hours']

                # Sum total dwell hours of enrolled students only
                total_dwell_hours = dwell_stats['Dwell_Hours'].sum()

                # Stats for clicks and last activity
                stats = student_logs.groupby(name_c).agg(Clicks=(time_c, 'count'), Last=(time_c, 'max')).reset_index()
                stats['Days_Since_Last'] = (max_log_time - stats['Last']).dt.days
                stats['Status'] = stats['Days_Since_Last'].apply(lambda x: "Active" if x < 14 else "Inactive")
                
                # Merge dwell + stats into df
                df = pd.merge(df, pd.merge(stats, dwell_stats, on=name_c), left_on='Name', right_on=name_c, how='left')
                
        except Exception as e:
            st.error(f"Error processing log CSV: {e}")
            
    return df, total_dwell_hours

def calculate_risk_scores(df, weight_config):
    """
    Calculates composite risk scores and determines risk categories.
    """
    # Ensure columns exist
    for col in ['Clicks', 'Dwell_Hours', 'Days_Since_Last']:
        if col not in df: df[col] = 0
    if 'Status' not in df: df['Status'] = "No Data"
    
    # Also ensure risk columns exist if empty to prevent downstream errors
    for col in ['Risk_Score', 'Final_Mark', 'Engagement_Score', 'Assignments_Gap', 'Quizzes_Gap']:
        if col not in df: df[col] = 0
    if 'Risk_Category' not in df: df['Risk_Category'] = "N/A"

    df = df.fillna(0)

    if df.empty:
        return df

    max_c = max(df['Clicks'].max(), 1)
    max_d = max(df['Dwell_Hours'].max(), 1)
    
    # Normalize Engagement to 100 (50% Clicks + 50% Dwell Time)
    df['Engagement_Score'] = (0.5 * (df['Clicks'] / max_c * 100) + 0.5 * (df['Dwell_Hours'] / max_d * 100)).round(1)
    
    # Assessment Completion (0-100)
    denom = max(1, len(weight_config))
    df['Assessment_Completion'] = 100 - ((df['Assignments_Gap'] + df['Quizzes_Gap']) / denom * 100)
    df['Assessment_Completion'] = df['Assessment_Completion'].clip(0, 100)
    
    # Performance Component (0-100)
    df['Performance_Component'] = df['Final_Mark']
    
    # Risk Score (0-100): 30% Engagement + 40% Assessment + 30% Performance
    # Risk = 100 - weighted_average
    df['Risk_Score'] = (100 - (0.3 * df['Engagement_Score'] + 0.4 * df['Assessment_Completion'] + 0.3 * df['Performance_Component'])).clip(0, 100).round(2)

    def determine_risk_category(row):
        # 1. CRITICAL: Missed 3+ Quizzes OR 2+ Assignments OR Risk Score > 75
        if row['Quizzes_Gap'] >= 3 or row['Assignments_Gap'] >= 2 or row['Risk_Score'] > 75:
            return '🔴 Critical'
        # 2. WARNING: Missed 2+ Quiz OR 1+ Assignment OR Risk Score > 50
        elif row['Quizzes_Gap'] >= 2 or row['Assignments_Gap'] >= 1 or row['Risk_Score'] > 50:
            return '🟡 Warning'
        # 3. SAFE
        else:
            return '🟢 Safe'

    df['Risk_Category'] = df.apply(determine_risk_category, axis=1)
    return df
