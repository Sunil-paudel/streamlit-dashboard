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

def clear_course_cache(course_id):
    """Clears all caches for a specific course to force a fresh fetch."""
    # 1. Clear Streamlit cache
    st.cache_data.clear()
    
    return True

def is_api_ready():
    return mc.check_connection()

@st.cache_data(ttl=3600)
def fetch_all_courses():
    try:
        courses = mc.get_courses()
        if isinstance(courses, dict) and courses.get('exception'):
            return pd.DataFrame()
        if not isinstance(courses, list):
            return pd.DataFrame()
            
        return pd.DataFrame(courses)
    except:
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_course_metadata(course_id):
    try:

        users = mc.get_enrolled_users(course_id)
        
        # Check for Moodle Exception or Error Code
        if isinstance(users, dict):
            if users.get('exception') or users.get('errorcode'):
                users = []
        elif not isinstance(users, list):
            users = []
            
        # Fail Fast: If users list is empty, assume token/course error and stop.
        if not users:
             return {'users': [], 'quizzes': [], 'assigns': [], 'submissions': {}, 'quiz_attempts': {}, 'groups': [], 'groupings': [], 'group_membership': {}, 'user_to_groups': {}}
        
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
        
        # --- Group & Grouping Data (ULTRA-ROBUST EXPLICIT METHOD) ---
        # 1. Fetch all course groups first (to ensure we miss nothing)
        all_groups_raw = mc.moodle_call("core_group_get_course_groups", {"courseid": course_id})
        if isinstance(all_groups_raw, dict) and "groups" in all_groups_raw:
            all_groups_raw = all_groups_raw.get("groups", [])
        elif not isinstance(all_groups_raw, list):
            all_groups_raw = []
        
        # 2. Fetch groupings to know which groups belong to which 'Class'
        basic_groupings = mc.moodle_call("core_group_get_course_groupings", {"courseid": course_id})
        if isinstance(basic_groupings, dict) and "groupings" in basic_groupings:
            basic_groupings = basic_groupings.get("groupings", [])
            
        detailed_groupings = []
        if basic_groupings:
            g_ids = [g['id'] for g in basic_groupings]
            params = {"returngroups": 1}
            for i, gid in enumerate(g_ids):
                params[f"groupingids[{i}]"] = gid
            detailed_groupings = mc.moodle_call("core_group_get_groupings", params) or basic_groupings

        # 3. Build mappings
        user_to_groups = {} # uid (str) -> [group_ids]
        group_membership = {} # group_id (str) -> [user_ids]
        
        for u in users:
            user_to_groups[str(u['id'])] = []

        # We first process ALL groups found in the course
        for group in all_groups_raw:
            gr_id = group.get("id")
            if not gr_id: continue
            
            # Fetch members explicitly
            params_mem = {"groupids[0]": gr_id}
            resp_mem = mc.moodle_call("core_group_get_groups_members", params_mem, silent=True)
            
            member_ids = []
            if isinstance(resp_mem, list) and resp_mem:
                member_ids = resp_mem[0].get("userids", [])
            
            if not member_ids:
                resp_fallback = mc.moodle_call("core_group_get_group_members", params_mem, silent=True)
                if isinstance(resp_fallback, list) and resp_fallback:
                    member_ids = resp_fallback[0].get("userids", [])

            group_membership[str(gr_id)] = member_ids
            for m_id in member_ids:
                actual_uid = str(m_id.get('id') if isinstance(m_id, dict) else m_id)
                if actual_uid in user_to_groups:
                    if gr_id not in user_to_groups[actual_uid]:
                        user_to_groups[actual_uid].append(gr_id)

        metadata = {
            'users': users,
            'quizzes': quizzes,
            'assigns': assigns,
            'submissions': submissions,
            'quiz_attempts': quiz_attempts,
            'groups': all_groups_raw,
            'groupings': detailed_groupings,
            'group_membership': group_membership,
            'user_to_groups': user_to_groups
        }
        
        return metadata
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

def sync_grade_to_moodle(course_id, user_id, item_id, item_type, grade_value, item_cmid=None, apply_to_all=False, max_grade=100.0):
    """
    Syncs a manually adjusted grade to Moodle.
    
    Args:
        course_id: The course ID
        user_id: The student's user ID
        item_id: The assignment or quiz ID
        item_type: 'assign' or 'quiz'
        grade_value: The raw grade value (not percentage)
        item_cmid: The course module ID (required for quizzes)
        apply_to_all: If True, applies to all members of the user's group (assignments only)
        max_grade: The maximum allowed grade for this item
    
    Returns:
        (success: bool, message: str)
    """
    try:
        # Clamp grade to max_grade to prevent DML errors
        if float(grade_value) > float(max_grade):
             grade_value = float(max_grade)
             # st.write(f"⚠️ Grade clamped to max {max_grade}")

        if item_type == 'assign':
            # Use mod_assign_save_grade for assignments
            moodle_apply = 1 if apply_to_all else 0
            result = mc.update_assignment_grade(item_id, user_id, grade_value, apply_to_all=moodle_apply)
            # Moodle might return None, an empty list [], or a dict without an exception on success
            if result is None or not isinstance(result, dict) or not result.get('exception'):
                suffix = " (applied to all group members)" if apply_to_all else ""
                return True, f"Successfully updated assignment grade for user {user_id}{suffix}"
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
