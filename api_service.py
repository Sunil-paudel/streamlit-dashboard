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
        
        # --- Group & Grouping Data ---
        groupings = mc.get_course_groupings(course_id) or []
        groups = mc.get_course_groups(course_id) or []
        
        # Build student-group mapping: user_id -> [group_ids]
        group_membership = {} # group_id -> [user_ids]
        group_ids = [g['id'] for g in groups]
        if group_ids:
            members_list = mc.get_groups_members(group_ids) or []
            for g_mem in members_list:
                group_membership[g_mem['groupid']] = g_mem.get('userids', [])
        
        # Mapping: user_id -> list of group_ids
        user_to_groups = {}
        for g_id, u_ids in group_membership.items():
            for u_id in u_ids:
                if u_id not in user_to_groups:
                    user_to_groups[u_id] = []
                user_to_groups[u_id].append(g_id)
        
        # Get detailed groupings to know which groups belong to which grouping
        detailed_groupings = []
        if groupings:
            grouping_ids = [g['id'] for g in groupings]
            detailed_groupings = mc.get_groupings_detailed(grouping_ids) or groupings
        
        return {
            'users': users,
            'quizzes': quizzes,
            'assigns': assigns,
            'submissions': submissions,
            'quiz_attempts': quiz_attempts,
            'groups': groups,
            'groupings': detailed_groupings,
            'group_membership': group_membership, # group_id -> [user_ids]
            'user_to_groups': user_to_groups     # user_id -> [group_ids]
        }
    except Exception as e:
        st.warning(f"Failed to fetch course metadata for ID {course_id}. Error: {e}")
        return {'users': [], 'quizzes': [], 'assigns': [], 'submissions': {}, 'quiz_attempts': {}, 'groups': [], 'groupings': [], 'group_membership': {}, 'user_to_groups': {}}

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

def sync_grade_to_moodle(course_id, user_id, item_id, item_type, grade_value, item_cmid=None):
    """
    Syncs a manually adjusted grade to Moodle.
    
    Args:
        course_id: The course ID
        user_id: The student's user ID
        item_id: The assignment or quiz ID
        item_type: 'assign' or 'quiz'
        grade_value: The raw grade value (not percentage)
        item_cmid: The course module ID (required for quizzes)
    
    Returns:
        (success: bool, message: str)
    """
    try:
        if item_type == 'assign':
            # Use mod_assign_save_grade for assignments
            result = mc.update_assignment_grade(item_id, user_id, grade_value)
            # Moodle might return None, an empty list [], or a dict without an exception on success
            if result is None or not isinstance(result, dict) or not result.get('exception'):
                return True, f"Successfully updated assignment grade for user {user_id}"
            else:
                # If it is a dict with an exception, use the message
                error_msg = result.get('message', 'Unknown error')
                return False, f"Failed to update grade: {error_msg}"
        elif item_type == 'quiz':
            # Use core_grades_update_grades for quiz manual override
            # For quizzes, we need the course module ID (cmid), not the quiz ID
            if not item_cmid:
                return False, "Course module ID (cmid) is required for quiz grade sync"
            
            result = mc.update_quiz_grade(item_cmid, user_id, grade_value, course_id)
            # Moodle might return None, an empty list [], or a dict without an exception on success
            if result is None or not isinstance(result, dict) or not result.get('exception'):
                return True, f"Successfully updated quiz grade for user {user_id}"
            else:
                # If it is a dict with an exception, use the message
                error_msg = result.get('message', 'Unknown error')
                return False, f"Failed to update quiz grade: {error_msg}"
        else:
            return False, f"Unknown item type: {item_type}"
    except Exception as e:
        return False, f"Error syncing grade: {str(e)}"
