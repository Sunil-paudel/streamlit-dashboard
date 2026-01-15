# ========================================================================================
# File: api_service.py
# Description: Moodle API Interaction Layer with Caching.
#
# Purpose:
#   - Interfaces with the custom `moodle_client` module to fetch data from Moodle.
#   - Implements Streamlit caching (@st.cache_data) to optimize performance and
#     reduce API calls during user interaction.
#   - Provides clean functions for fetching courses, metadata (users/quizzes/assignments),
#     and user grades.
#
# Key Functions:
#   - fetch_all_courses: storage time 1 hour.
#   - fetch_course_metadata: storage time 30 mins.
#   - fetch_user_grades_batch: storage time 10 mins.
# ========================================================================================

import streamlit as st
import pandas as pd
import moodle_client as mc

def is_api_ready():
    return mc.check_connection()

@st.cache_data(ttl=3600)
def fetch_all_courses():
    try:
        return pd.DataFrame(mc.get_courses())
    except:
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_course_metadata(course_id):
    try:
        users = mc.get_enrolled_users(course_id) or []
        
        quizzes_res = mc.get_quizzes_by_courses(course_id) or {}
        quizzes = quizzes_res.get('quizzes', [])
        quiz_attempts = {}
        for q in quizzes:
            attempts_res = mc.get_all_quiz_attempts(q['id']) or {}
            # Map by Quiz ID then User ID
            quiz_attempts[q['id']] = {att['userid']: att for att in attempts_res.get('attempts', [])}
        
        assigns_res = mc.get_assignments([course_id]) or {}
        assigns = []
        submissions = {}
        if assigns_res.get('courses'):
            assigns = assigns_res['courses'][0].get('assignments', [])
            assign_ids = [a['id'] for a in assigns]
            if assign_ids:
                subs_res = mc.get_submissions(assign_ids) or {}
                # Map submissions by assignment ID and user ID
                for assignment in subs_res.get('assignments', []):
                    a_id = assignment['assignmentid']
                    submissions[a_id] = {s['userid']: s for s in assignment.get('submissions', [])}
            
        return users, quizzes, assigns, submissions, quiz_attempts
    except Exception as e:
        st.warning(f"Failed to fetch course metadata for ID {course_id}. Error: {e}")
        return [], [], [], {}, {}

@st.cache_data(ttl=600)
def fetch_user_grades_batch(course_id, user_id):
    try:
        res = mc.get_user_grades(course_id, user_id)
        if 'usergrades' in res and len(res['usergrades']) > 0:
            return res['usergrades'][0].get('gradeitems', [])
        return res.get('gradeitems', [])
    except:
        return []

@st.cache_data(ttl=600)
def fetch_completion_status(course_id, user_id):
    """Fetches the completion status of all activities in a course for a user."""
    try:
        res = mc.get_completion_status(course_id, user_id)
        return res.get('statuses', [])
    except:
        return []
