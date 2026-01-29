# ========================================================================================
# File: moodle_client.py
# Description: Low-level Moodle API Client Library.
#
# Purpose:
#   - Handles direct HTTP requests to the Moodle configuration.
#   - Encapsulates authentication (Token) and parameters.
#   - Provides specific wrapper functions for various Moodle Web Service API endpoints
#     (e.g., core_course_get_courses, gradereport_user_get_grade_items).
#   - Implements short-term caching (ttl=60s) for raw API responses to prevent
#     rate-limiting during rapid development/testing.
#
# Usage:
#   - Imported by `api_service.py` which provides a higher-level abstraction.
#
# Dependencies:
#   - requests (HTTP calls)
#   - streamlit (st.cache_data)
# ========================================================================================

import os
import requests
import streamlit as st
from dotenv import load_dotenv
load_dotenv()


# ---------- config ----------
MOODLE_URL = os.getenv("MOODLE_URL")
TOKEN      = os.getenv("MOODLE_TOKEN")

# Normalize URL
if MOODLE_URL and MOODLE_URL.endswith('/'):
    MOODLE_URL = MOODLE_URL[:-1]

ENDPOINT = f"{MOODLE_URL}/webservice/rest/server.php" if MOODLE_URL else None

def check_connection():
    """Returns (bool, message) about the Moodle connection status."""
    if not MOODLE_URL or "your-moodle-site" in MOODLE_URL:
        return False, "Moodle URL is missing or default."
    if not TOKEN or "your-token-here" in TOKEN:
        return False, "Moodle API Token is missing or default."
    return True, "Configuration present."

# ---------- low-level caller ----------
def moodle_call(function, params=None, silent=False, method="GET"):
    is_ok, msg = check_connection()
    if not is_ok:
        return {}

    try:
        payload = {
            "wstoken": TOKEN,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **(params or {})
        }
        if method.upper() == "POST":
            r = requests.post(ENDPOINT, data=payload, timeout=15)
        else:
            r = requests.get(ENDPOINT, params=payload, timeout=10)
        
        r.raise_for_status()
        json_res = r.json()
        
        # Handle Moodle-specific exceptions returned in JSON
        if isinstance(json_res, dict) and json_res.get("exception"):
            if not silent:
                # Check for invalid token specifically
                if json_res.get("errorcode") == "invalidtoken":
                     st.error("Invalid Moodle Token: Please verify your token in the settings.")
                else:
                     st.error(f"Moodle API Exception ({function}): {json_res.get('message')}")
                     st.write(f"DEBUG - Full error response: {json_res}")
            return json_res
            
        return json_res
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"Moodle URL Not Found: The URL `{MOODLE_URL}` seems incorrect (404).")
        else:
            st.error(f"HTTP Error: {e}")
        return {}
    except requests.exceptions.RequestException as e:
        st.error(f"Connection Error: Unable to reach Moodle at `{MOODLE_URL}`. Please check your internet or URL.")
        return {}
    except Exception as e:
        st.error(f"Unexpected Error: {e}")
        return {}

# ---------- existing high-level helpers ----------
@st.cache_data(ttl=60)
def get_user_by_field(field, value):
    return moodle_call("core_user_get_users_by_field",
                      {"field": field, f"values[0]": value})

@st.cache_data(ttl=60)
def get_user_courses(userid):
    return moodle_call("core_enrol_get_users_courses", {"userid": userid})

@st.cache_data(ttl=60)
def get_user_grades(courseid, userid):
    return moodle_call("gradereport_user_get_grade_items",
                      {"courseid": courseid, "userid": userid})

@st.cache_data(ttl=60)
def get_courses():
    return moodle_call("core_course_get_courses")

@st.cache_data(ttl=60)
def get_enrolled_users(courseid):
    return moodle_call("core_enrol_get_enrolled_users", {"courseid": courseid})

@st.cache_data(ttl=60)
def get_assignments(courseids):
    params = {}
    for i, cid in enumerate(courseids):
        params[f"courseids[{i}]"] = cid
    return moodle_call("mod_assign_get_assignments", params)

@st.cache_data(ttl=60)
def get_submissions(assignids):
    params = {}
    for i, aid in enumerate(assignids):
        params[f"assignmentids[{i}]"] = aid
    return moodle_call("mod_assign_get_submissions", params)

@st.cache_data(ttl=60)
def get_completion_status(courseid, userid):
    return moodle_call("core_completion_get_activities_completion_status",
                      {"courseid": courseid, "userid": userid})

@st.cache_data(ttl=60)
def get_quizzes_by_courses(courseid):
    params = {"courseids[0]": courseid}
    return moodle_call("mod_quiz_get_quizzes_by_courses", params)

@st.cache_data(ttl=60)
def get_all_quiz_attempts(quizid, status="all"):
    """Get all attempts for a specific quiz ID across all users."""
    # We use silent=True here because some quizzes might throw "Record not found" 
    # if they are in a strange state in Moodle (e.g. newly created or hidden)
    return moodle_call("mod_quiz_get_attempts", {"quizid": quizid, "status": status}, silent=True)

@st.cache_data(ttl=60)
def get_course_groupings(courseid):
    res = moodle_call("core_group_get_course_groupings", {"courseid": courseid})
    if isinstance(res, dict) and 'groupings' in res:
        return res['groupings']
    return res if isinstance(res, list) else []

@st.cache_data(ttl=60)
def get_groupings_detailed(groupingids):
    params = {"returngroups": 1}
    for i, gid in enumerate(groupingids):
        params[f"groupingids[{i}]"] = gid
    res = moodle_call("core_group_get_groupings", params)
    # The response can be a list or a dict containing a list
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and 'groupings' in res:
        return res['groupings']
    return []

@st.cache_data(ttl=60)
def get_course_groups(courseid):
    res = moodle_call("core_group_get_course_groups", {"courseid": courseid})
    if isinstance(res, dict) and 'groups' in res:
        return res['groups']
    return res if isinstance(res, list) else []

@st.cache_data(ttl=60)
def get_groups_members(groupids):
    """Get members for multiple groups. Returns standardized list of dicts."""
    if not groupids: return []
    params = {}
    for i, gid in enumerate(groupids):
        params[f"groupids[{i}]"] = gid
    res = moodle_call("core_group_get_groups_members", params, silent=True)
    
    # Fallback to singular method if batch fails or returns exception
    if not res or (isinstance(res, dict) and res.get('exception')):
        results = []
        for gid in groupids:
            s_res = moodle_call("core_group_get_group_members", {"groupid": gid}, silent=True)
            if isinstance(s_res, list):
                results.append({'groupid': gid, 'userids': s_res})
            elif isinstance(s_res, dict) and 'userids' in s_res:
                results.append({'groupid': gid, 'userids': s_res['userids']})
        return results
    return res if isinstance(res, list) else []

@st.cache_data(ttl=300)
def get_course_user_groups(courseid, userids=None):
    """Get all groups for users in a course. Returns a list of groups per user."""
    params = {"courseid": courseid}
    if userids:
        for i, uid in enumerate(userids):
            params[f"userids[{i}]"] = uid
    return moodle_call("core_group_get_course_user_groups", params)

def update_assignment_grade(assignment_id, user_id, grade, apply_to_all=0):
    """
    Updates a student's grade for an assignment using mod_assign_save_grade.
    
    Args:
        assignment_id: The assignment ID
        user_id: The student's user ID
        grade: The raw grade value (not percentage)
        apply_to_all: 1 to apply to all group members, 0 otherwise
    
    Returns:
        API response dict
    """
    params = {
        'assignmentid': assignment_id,
        'userid': user_id,
        'grade': grade,
        'attemptnumber': -1,  # -1 means the latest attempt
        'addattempt': 0,
        'workflowstate': 'released',
        'applytoall': apply_to_all
    }
    return moodle_call("mod_assign_save_grade", params, method="POST")

def update_quiz_grade(quiz_cmid, user_id, grade, course_id):
    """
    Updates a student's grade for a quiz using core_grades_update_grades.
    This creates a manual grade override in the gradebook.
    
    Args:
        quiz_cmid: The quiz course module ID (not the quiz ID)
        user_id: The student's user ID
        grade: The raw grade value (not percentage)
        course_id: The course ID
    
    Returns:
        API response dict
    """
    params = {
        'source': 'mod/quiz',
        'courseid': course_id,
        'component': 'mod_quiz',
        'activityid': quiz_cmid,
        'itemnumber': 0,
        'grades[0][studentid]': user_id,
        'grades[0][grade]': grade
    }
    return moodle_call("core_grades_update_grades", params)