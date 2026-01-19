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
def moodle_call(function, params=None, silent=False):
    is_ok, msg = check_connection()
    if not is_ok:
        # We don't want to spam errors if we know it's not configured
        return {}

    try:
        payload = {
            "wstoken": TOKEN,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **(params or {})
        }
        r = requests.get(ENDPOINT, params=payload, timeout=10)
        r.raise_for_status()
        json = r.json()
        
        # Handle Moodle-specific exceptions returned in JSON
        if isinstance(json, dict) and json.get("exception"):
            if not silent:
                # Check for invalid token specifically
                if json.get("errorcode") == "invalidtoken":
                     st.error("🔑 **Invalid Moodle Token**: Please verify your token in the settings.")
                else:
                     st.error(f"Moodle API Exception ({function}): {json.get('message')}")
                     st.write(f"DEBUG - Full error response: {json}")
            return {}
            
        return json
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"🌐 **Moodle URL Not Found**: The URL `{MOODLE_URL}` seems incorrect (404).")
        else:
            st.error(f"HTTP Error: {e}")
        return {}
    except requests.exceptions.RequestException as e:
        st.error(f"📡 **Connection Error**: Unable to reach Moodle at `{MOODLE_URL}`. Please check your internet or URL.")
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

def update_assignment_grade(assignment_id, user_id, grade):
    """
    Updates a student's grade for an assignment using mod_assign_save_grade.
    
    Args:
        assignment_id: The assignment ID
        user_id: The student's user ID
        grade: The raw grade value (not percentage)
    
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
        'applytoall': 0
    }
    return moodle_call("mod_assign_save_grade", params)

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