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
def get_completion_status(courseid, userid):
    return moodle_call("core_completion_get_activities_completion_status",
                      {"courseid": courseid, "userid": userid})

# ---------- new completion functions ----------
@st.cache_data(ttl=60)
def get_course_completion_status(courseid, userid):
    """Returns course completion status for a user."""
    return moodle_call("core_completion_get_course_completion_status",
                      {"courseid": courseid, "userid": userid})

# ---------- course functions ----------
@st.cache_data(ttl=60)
def get_course_contents(courseid):
    """Get course contents including sections and activities."""
    return moodle_call("core_course_get_contents",
                      {"courseid": courseid})

# ---------- enrollment functions ----------
@st.cache_data(ttl=60)
def get_enrolled_users(courseid):
    """Get all enrolled users in a course."""
    return moodle_call("core_enrol_get_enrolled_users",
                      {"courseid": courseid})

# ---------- grade functions ----------
@st.cache_data(ttl=60)
def update_grades(source, courseid, component, activityid, itemnumber, grades, itemdetails=None):
    """Update a grade item and associated student grades."""
    params = {
        "source": source,
        "courseid": courseid,
        "component": component,
        "activityid": activityid,
        "itemnumber": itemnumber,
    }
    # Add grades array
    for i, grade in enumerate(grades):
        for key, val in grade.items():
            params[f"grades[{i}][{key}]"] = val
    # Add itemdetails if provided
    if itemdetails:
        for key, val in itemdetails.items():
            params[f"itemdetails[{key}]"] = val
    return moodle_call("core_grades_update_grades", params)

@st.cache_data(ttl=60)
def get_course_grades_overview(userid):
    """Get the user's final grades for all their courses."""
    return moodle_call("gradereport_overview_get_course_grades",
                      {"userid": userid})

@st.cache_data(ttl=60)
def get_grade_items_for_search(courseid, searchvalue):
    """Get grade items for a course (search widget)."""
    return moodle_call("gradereport_singleview_get_grade_items_for_search_widget",
                      {"courseid": courseid, "searchvalue": searchvalue})

@st.cache_data(ttl=60)
def get_grades_table(courseid, userid, groupid=0):
    """Get the grades table for a user in a course."""
    return moodle_call("gradereport_user_get_grades_table",
                      {"courseid": courseid, "userid": userid, "groupid": groupid})

# ---------- user profile functions ----------
@st.cache_data(ttl=60)
def get_course_user_profiles(userlist):
    """Get course user profiles.
    
    Args:
        userlist: List of dicts with 'userid' and 'courseid' keys
    """
    params = {}
    for i, user in enumerate(userlist):
        params[f"userlist[{i}][userid]"] = user["userid"]
        params[f"userlist[{i}][courseid]"] = user["courseid"]
    return moodle_call("core_user_get_course_user_profiles", params)

# ---------- assignment functions ----------
@st.cache_data(ttl=60)
def get_assignments(courseids=None):
    """Get assignments. If courseids not provided, returns all visible assignments."""
    params = {}
    if courseids:
        for i, cid in enumerate(courseids):
            params[f"courseids[{i}]"] = cid
    return moodle_call("mod_assign_get_assignments", params)

@st.cache_data(ttl=60)
def get_submissions(assignmentids):
    """Get submissions for assignments."""
    params = {}
    for i, aid in enumerate(assignmentids):
        params[f"assignmentids[{i}]"] = aid
    return moodle_call("mod_assign_get_submissions", params)

# ---------- forum functions ----------
@st.cache_data(ttl=60)
def get_forum_discussions(forumid, sortorder=None, page=0, perpage=0):
    """Get forum discussions with optional sorting and pagination."""
    params = {"forumid": forumid, "page": page, "perpage": perpage}
    if sortorder:
        params["sortorder"] = sortorder
    return moodle_call("mod_forum_get_forum_discussions", params)

# ---------- quiz functions ----------


@st.cache_data(ttl=60)
def get_user_best_grade(quizid, userid):
    """Get the best current grade for a user on a quiz."""
    return moodle_call("mod_quiz_get_user_best_grade",
                      {"quizid": quizid, "userid": userid})

@st.cache_data(ttl=60)
def get_user_quiz_attempts(quizid, userid, status="all", includepreviews=False):
    """Get all attempts for a user on a quiz.
    
    Args:
        status: 'all', 'finished', or 'unfinished'
        includepreviews: Whether to include preview attempts
    """
    return moodle_call("mod_quiz_get_user_quiz_attempts",
                      {"quizid": quizid, "userid": userid, 
                       "status": status, "includepreviews": int(includepreviews)})

@st.cache_data(ttl=300)
def get_courses():
    """Fetches all courses available to the API token."""
    return moodle_call("core_course_get_courses")

@st.cache_data(ttl=60)
def get_quizzes_by_courses(courseid):
    """Get quizzes for a specific course ID."""
    # Moodle REST requires array parameters to be explicitly indexed
    params = {"courseids[0]": courseid} 
    return moodle_call("mod_quiz_get_quizzes_by_courses", params)

@st.cache_data(ttl=60)
def get_all_quiz_attempts(quizid, status="all"):
    """Get all attempts for a specific quiz ID across all users."""
    # We use silent=True here because some quizzes might throw "Record not found" 
    # if they are in a strange state in Moodle (e.g. newly created or hidden)
    return moodle_call("mod_quiz_get_attempts", {"quizid": quizid, "status": status}, silent=True)