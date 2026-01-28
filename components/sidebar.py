import streamlit as st
import pandas as pd
from datetime import datetime
from config import COORD_EMAIL
from api_service import fetch_all_courses, fetch_course_metadata, is_api_ready
from data_processing import get_log_date_range

def render_sidebar():
    """
    Renders the sidebar for course selection, log upload, and configuration.
    
    Returns:
        tuple: (
            course_id (int),
            log_file (file object),
            start_date (date),
            end_date (date),
            weight_config (dict),
            formula_config (dict),
            total_target (float),
            coord_email (str),
            users_raw (list),
            submission_data (dict),
            quiz_attempts_raw (dict)
        )
    """
    # ================== 3. API STATUS CHECK ==================
    api_ok, api_msg = is_api_ready()
    if not api_ok:
        st.sidebar.error(f"Moodle Connection Issue\n\n{api_msg}")

        return None
    else:
        # Add a Refresh Button to clear cache
        if st.sidebar.button("Refresh Course Data"):

            st.cache_data.clear()
            st.rerun()

    # ================== 4. SIDEBAR COURSE & WEIGHT CONFIG ==================
    st.sidebar.header("Course Setup")

    courses_df = fetch_all_courses()
    course_id = 1
    
    if not courses_df.empty:
        # Check if 'id' and 'fullname' exist
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

    st.sidebar.markdown("---")
    coord_email = st.sidebar.text_input("Coordinator Email", value=COORD_EMAIL)
    st.sidebar.markdown("---")
    st.sidebar.subheader("Assessment Weight Setup")


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
        
    return (
        course_id, log_file, start_date, end_date, weight_config, formula_config, 
        total_target, coord_email, users_raw, submission_data, quiz_attempts_raw
    )
