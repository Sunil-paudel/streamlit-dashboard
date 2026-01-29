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
import time
from api_service import fetch_user_grades_batch, fetch_completion_status
from utils import calculate_dwell_time

def calculate_student_metrics(users_raw, weight_config, course_id, submission_data=None, quiz_attempts=None):
    """
    Iterates through enrolled students and calculates their incomplete assignments,
    quizzes, and current weighted marks, as well as submission timing.
    """
    student_results = []
    teacher_results = []
    staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator', 'staff', 'grader', 'admin', 'administrator']

    for user in users_raw:
        # Check both shortname and name for safety
        user_roles = []
        for r in user.get('roles', []):
            if r.get('shortname'): user_roles.append(r['shortname'].lower())
            if r.get('name'): user_roles.append(r['name'].lower())
            
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
        completion_statuses = fetch_completion_status(course_id, user['id'])

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
                    g_type = g.get('itemtype')

                    if g_module != 'assign' or g_type != 'mod':
                        continue

                    # Match by ID
                    if g_inst and int(g_inst) == config['id']:
                        report_raw = float(g.get('graderaw') if g.get('graderaw') is not None else 0.0)
                        report_max = float(g.get('grademax') if (g.get('grademax') is not None and float(g.get('grademax')) > 0) else config.get('grademax', 100.0))
                        native_max = float(config.get('grademax', 100.0))
                        
                        # Normalize to native scale if Moodle Gradebook has scaled it
                        if report_max > 0 and abs(report_max - native_max) > 0.001:
                            r_ob = (report_raw / report_max) * native_max
                        else:
                            r_ob = report_raw
                        m_ob = native_max
                        
                        matched_grade_id = g_id
                        break

                    # Exact name match
                    elif g_name == config['name'].lower().strip():
                        report_raw = float(g.get('graderaw') if g.get('graderaw') is not None else 0.0)
                        report_max = float(g.get('grademax') if (g.get('grademax') is not None and float(g.get('grademax')) > 0) else config.get('grademax', 100.0))
                        native_max = float(config.get('grademax', 100.0))
                        
                        if report_max > 0 and abs(report_max - native_max) > 0.001:
                            r_ob = (report_raw / report_max) * native_max
                        else:
                            r_ob = report_raw
                        m_ob = native_max
                        
                        matched_grade_id = g_id
                        break

                if matched_grade_id:
                    matched_items.add(matched_grade_id)

                # Check if it's already overdue
                # due_timestamp is from config.get('duedate',0)
                now = time.time()
                is_overdue = (due_timestamp > 0 and now > due_timestamp)
                
                row[f"overdue_{key}"] = is_overdue

                # Check completion/viewed status
                is_viewed = False
                cmid = config.get('cmid')
                if cmid:
                    comp = next((c for c in completion_statuses if c.get('cmid') == cmid), None)
                    if comp and comp.get('viewed') == 1:
                        is_viewed = True
                row[f"viewed_{key}"] = is_viewed

                if r_ob == 0.0:
                    # If no grade found or grade is 0, use grademax as the denominator for gaps
                    # but only if it's actually overdue or we need a denominator
                    if m_ob <= 0:
                        m_ob = config.get('grademax', 100.0)
                    
                    if is_overdue:
                        row['Assignments_Gap'] += 1

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
                    g_type = g.get('itemtype')
                    if g_module != 'quiz' or g_type != 'mod':
                        continue

                    # Match by ID
                    if g_inst and int(g_inst) == config['id']:
                        report_raw = float(g.get('graderaw') if g.get('graderaw') is not None else 0.0)
                        report_max = float(g.get('grademax') if (g.get('grademax') is not None and float(g.get('grademax')) > 0) else config.get('grademax', 100.0))
                        native_max = float(config.get('grademax', 100.0))
                        
                        if report_max > 0 and abs(report_max - native_max) > 0.001:
                            r_ob = (report_raw / report_max) * native_max
                        else:
                            r_ob = report_raw
                        m_ob = native_max
                        
                        matched_grade_id = g.get('id')
                        break

                    # Name match
                    elif g_name == config['name'].lower().strip():
                        report_raw = float(g.get('graderaw') if g.get('graderaw') is not None else 0.0)
                        report_max = float(g.get('grademax') if (g.get('grademax') is not None and float(g.get('grademax')) > 0) else config.get('grademax', 100.0))
                        native_max = float(config.get('grademax', 100.0))
                        
                        if report_max > 0 and abs(report_max - native_max) > 0.001:
                            r_ob = (report_raw / report_max) * native_max
                        else:
                            r_ob = report_raw
                        m_ob = native_max
                        
                        matched_grade_id = g.get('id')
                        break

                if matched_grade_id:
                    matched_items.add(matched_grade_id)

                now = time.time()
                # A quiz is considered 'overdue' if:
                # 1. It has a due_timestamp in the past
                # 2. OR it has NO due_timestamp but is currently 'visible'
                is_visible = config.get('visible', 1) == 1
                is_overdue = (due_timestamp > 0 and now > due_timestamp) or (due_timestamp == 0 and is_visible)
                row[f"overdue_{key}"] = is_overdue

                # Check for "In Progress" status
                status_in_progress = False
                if quiz_attempts and config['id'] in quiz_attempts:
                    user_att = quiz_attempts[config['id']].get(user['id'])
                    if user_att and user_att.get('state') == 'inprogress':
                        status_in_progress = True
                
                row[f"inprogress_{key}"] = status_in_progress

                # Check completion/viewed status
                is_viewed = False
                cmid = config.get('cmid')
                if cmid:
                    comp = next((c for c in completion_statuses if c.get('cmid') == cmid), None)
                    if comp and comp.get('viewed') == 1:
                        is_viewed = True
                row[f"viewed_{key}"] = is_viewed

                if r_ob == 0.0:
                    # If we have no grade, the denominator for the risk pool should be the actual max grade
                    if m_ob <= 0:
                        m_ob = config.get('grademax', 100.0)
                    
                    if is_overdue:
                        row['Quizzes_Gap'] += 1
                else:
                    # If we HAVE a grade, but grademax is missing or 0 (prevent div by zero)
                    if m_ob <= 0:
                        m_ob = config.get('grademax', 100.0)

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
        row['Early_Warning'] = "Flagged" if (row['Assignments_Gap'] > 0 or row['Quizzes_Gap'] >= 2) else "Normal"
        student_results.append(row)
        
    return student_results, teacher_results

def get_log_date_range(log_file):
    """
    Quickly scans the log file to determine the min and max dates.
    """
    if not log_file:
        return None, None
    try:
        # Read the file again to avoid messing with the main stream if needed
        # but Streamlit file_uploader is a stream, so we should be careful.
        # However, for CSVs we can just read the time column.
        log_file.seek(0)
        df_log = pd.read_csv(log_file, usecols=lambda c: 'time' in c.lower(), on_bad_lines='skip', engine='python')
        log_file.seek(0) # Reset stream
        
        time_c = next((c for c in df_log.columns if 'time' in c.lower()), None)
        if time_c:
            times = pd.to_datetime(df_log[time_c], errors='coerce', dayfirst=True, format='mixed').dropna()
            if not times.empty:
                return times.min().date(), times.max().date()
    except Exception as e:
        st.error(f"Error reading log dates: {e}")
    return None, None

def process_logs_and_merge(df, log_file, users_raw, start_date=None, end_date=None):
    """
    Processes the uploaded Moodle log file and merges dwell time / activity stats 
    into the main student DataFrame, restricted by a specific date range.
    """
    total_dwell_hours = 0.0
    # Normalize inputs to datetime64[ns] for comparison if they are datetime.date
    if start_date:
        start_date = pd.to_datetime(start_date)
    if end_date:
        # Extend to end of day
        end_date = pd.to_datetime(end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    if log_file:
        try:
            logs = pd.read_csv(log_file, on_bad_lines='skip', engine='python', encoding='utf-8')
            time_c = next((c for c in logs.columns if 'time' in c.lower()), None)
            name_c = next((c for c in logs.columns if 'name' in c.lower()), None)
            if time_c and name_c:
                logs[time_c] = pd.to_datetime(logs[time_c], errors='coerce', dayfirst=True, format='mixed')
                logs = logs.dropna(subset=[time_c])

                # Identify the reference "now" from the logs (for Days_Since_Last)
                max_log_time = logs[time_c].max()
                
                # Filter logs by the selected absolute range
                if start_date and end_date:
                    logs = logs[(logs[time_c] >= start_date) & (logs[time_c] <= end_date)]
                
                # Calculate effective window days for weekly normalization
                if start_date and end_date:
                    window_days = (end_date - start_date).days + 1
                else:
                    window_days = 7 # fallback

                # Create a set of enrolled student names (exclude staff)
                staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator', 'staff', 'grader', 'admin', 'administrator']
                student_names = []
                for u in users_raw:
                    u_roles = []
                    for r in u.get('roles', []):
                        if r.get('shortname'): u_roles.append(r['shortname'].lower())
                        if r.get('name'): u_roles.append(r['name'].lower())
                    
                    if not any(role in staff_roles for role in u_roles):
                        student_names.append(u['fullname'])
                
                student_names_lower = [n.lower() for n in student_names]

                # Normalize log names
                logs['Name_LC'] = logs[name_c].str.lower().str.strip()
                student_logs = logs[logs['Name_LC'].isin(student_names_lower)]

                if student_logs.empty:
                    # No matching logs for students
                    return df, 0.0

                # Compute dwell hours for only enrolled students
                dwell_stats = student_logs.groupby(name_c).apply(lambda x: calculate_dwell_time(x, time_c), include_groups=False).reset_index()
                dwell_stats.columns = [name_c, 'Dwell_Hours']

                # Sum total dwell hours of enrolled students only
                total_dwell_hours = dwell_stats['Dwell_Hours'].sum()

                # Stats for clicks and last activity
                stats = student_logs.groupby(name_c).agg(Clicks=(time_c, 'count'), Last=(time_c, 'max')).reset_index()
                stats['Days_Since_Last'] = (max_log_time - stats['Last']).dt.days
                stats['Status'] = stats['Days_Since_Last'].apply(lambda x: "Active" if x < 14 else "Inactive")
                
                # Normalize clicks to per-week basis
                if window_days and window_days > 0:
                    stats['Clicks_Per_Week'] = (stats['Clicks'] / window_days * 7).round(2)
                else:
                    stats['Clicks_Per_Week'] = stats['Clicks']

                # Merge dwell + stats into df
                df = pd.merge(df, pd.merge(stats, dwell_stats, on=name_c), left_on='Name', right_on=name_c, how='left')
            else:
                st.warning("Could not detect 'Time' or 'User full name' columns in the log CSV.")
                
        except Exception as e:
            if not users_raw:
                st.info("Please choose a Course in the sidebar to link activity logs with students.")
            else:
                st.error(f"Error processing log CSV: {e}")
            
    return df, total_dwell_hours

def calculate_risk_scores(df, weight_config, formula_config=None):
    """
    Calculates composite risk scores and determines risk categories.
    """
    # Default weights if not provided
    if formula_config is None:
        formula_config = {
            'activity_weight': 0.5,
            'completion_weight': 0.5,
            'engagement_overall_weight': 0.6,
            'performance_overall_weight': 0.4
        }
    
    activity_w = formula_config.get('activity_weight', 0.5)
    completion_w = formula_config.get('completion_weight', 0.5)
    engagement_ow = formula_config.get('engagement_overall_weight', 0.6)
    performance_ow = formula_config.get('performance_overall_weight', 0.4)

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

    # Compute Dwell and Engagement components
    max_c = max(df['Clicks'].max(), 1)
    max_d = max(df['Dwell_Hours'].max(), 1)
    
    # 1. Activity Component (0-100)
    df['Activity_Score'] = (0.5 * (df['Clicks'] / max_c * 100) + 0.5 * (df['Dwell_Hours'] / max_d * 100)).round(2)
    
    # 2. Assessment Completion (0-100)
    def calculate_completion(row):
        total_due = 0
        completed_due = 0
        for key in weight_config.keys():
            is_overdue = row.get(f"overdue_{key}", False)
            points = row.get(f"pts_{key}", 0.0)
            
            if is_overdue or points > 0:
                total_due += 1
                if points > 0:
                    completed_due += 1
        
        if total_due == 0:
            return 100.0
        return (completed_due / total_due) * 100.0

    df['Assessment_Completion'] = df.apply(calculate_completion, axis=1)
    
    # Unified Engagement Score (0-100): Weighted Activity + Weighted Completion
    df['Engagement_Score'] = (activity_w * df['Activity_Score'] + completion_w * df['Assessment_Completion']).round(2)

    # 3. Performance Component (0-100)
    # We normalize marks relative to what has been released/submitted so far
    def calculate_performance(row):
        achieved = row.get('Final_Mark', 0.0)
        current_weight_pool = 0.0
        for key, cfg in weight_config.items():
            pts = row.get(f"pts_{key}", 0.0)
            is_overdue = row.get(f"overdue_{key}", False)
            if pts > 0 or is_overdue:
                current_weight_pool += cfg['weight']
        
        if current_weight_pool <= 0:
            return 100.0 # Neutral/Safe if nothing is due yet
        return min(100.0, (achieved / current_weight_pool) * 100.0)

    df['Performance_Component'] = df.apply(calculate_performance, axis=1)
    
    # Risk Score (0-100): Weighted Engagement + Weighted Performance
    # Risk = 100 - weighted_average
    df['Risk_Score'] = (100 - (engagement_ow * df['Engagement_Score'] + performance_ow * df['Performance_Component'])).clip(0, 100).round(2)

    def determine_risk_category(row):
        # 1. CRITICAL: Missed 3+ Overdue/Not-Participated Quizzes OR 2+ Overdue Assignments OR Risk Score > 75
        if row['Quizzes_Gap'] >= 3 or row['Assignments_Gap'] >= 2 or row['Risk_Score'] > 75:
            return 'Critical'
        # 2. WARNING: Missed 2+ Overdue/Not-Participated Quiz OR 1+ Overdue Assignment OR Risk Score > 50
        elif row['Quizzes_Gap'] >= 2 or row['Assignments_Gap'] >= 1 or row['Risk_Score'] > 50:
            return 'Warning'
        # 3. SAFE
        else:
            return 'Safe'

    df['Risk_Category'] = df.apply(determine_risk_category, axis=1)
    return df

def aggregate_weekly_activity(log_file, users_raw, start_date=None, end_date=None):
    """
    Parses logs to compute total clicks per week across the entire class.
    Returns a DataFrame suitable for a line chart (Date, Clicks).
    """
    if not log_file:
        return pd.DataFrame()

    try:
        # Read logs (lightweight read)
        log_file.seek(0)
        logs = pd.read_csv(log_file, on_bad_lines='skip', engine='python', encoding='utf-8')
        log_file.seek(0) # Reset pointer
        
        time_c = next((c for c in logs.columns if 'time' in c.lower()), None)
        name_c = next((c for c in logs.columns if 'name' in c.lower()), None)
        
        if not time_c or not name_c:
            return pd.DataFrame()

        logs[time_c] = pd.to_datetime(logs[time_c], errors='coerce', dayfirst=True, format='mixed')
        logs = logs.dropna(subset=[time_c])
        
        # Filter by Date Range
        if start_date:
            logs = logs[logs[time_c] >= pd.to_datetime(start_date)]
        if end_date:
             # Extend to end of day
            end_d = pd.to_datetime(end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59)
            logs = logs[logs[time_c] <= end_d]

        # Filter Enrolled Students Only (exclude staff/guests)
        staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator', 'staff', 'grader', 'admin', 'administrator']
        student_names = set()
        for u in users_raw:
            u_roles = []
            for r in u.get('roles', []):
                if r.get('shortname'): u_roles.append(r['shortname'].lower())
                if r.get('name'): u_roles.append(r['name'].lower())
            
            if not any(role in staff_roles for role in u_roles):
                student_names.add(u['fullname'].lower())

        logs['Name_LC'] = logs[name_c].str.lower().str.strip()
        student_logs = logs[logs['Name_LC'].isin(student_names)]
        
        if student_logs.empty:
            return pd.DataFrame()

        # Resample by Week (Mon-Sun)
        # Set index to time
        student_logs = student_logs.set_index(time_c)
        weekly_stats = student_logs.resample('W-MON').size().reset_index(name='Clicks')
        weekly_stats.columns = ['Week', 'Clicks']
        
        return weekly_stats

    except Exception as e:
        # print(f"Error aggregating weekly logs: {e}")
        return pd.DataFrame()
