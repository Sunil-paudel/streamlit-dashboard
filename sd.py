import os
from dotenv import load_dotenv
import requests
from datetime import datetime
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

# -------------------- Load Environment Variables --------------------
load_dotenv()
MOODLE_URL = os.getenv("MOODLE_URL")
MOODLE_TOKEN = os.getenv("MOODLE_TOKEN", "").strip()

if not MOODLE_URL or not MOODLE_TOKEN:
    st.error("⚠️ MOODLE_URL or MOODLE_TOKEN not set in .env")
    st.stop()

# Normalize URL
if MOODLE_URL.endswith('/'):
    MOODLE_URL = MOODLE_URL[:-1]

# -------------------- Moodle API Helper --------------------
def moodle_api_call(function_name: str, params=None):
    if params is None:
        params = {}
    url = f"{MOODLE_URL}/webservice/rest/server.php"
    payload = {
        "wstoken": MOODLE_TOKEN,
        "moodlewsrestformat": "json",
        "wsfunction": function_name,
        **params
    }
    try:
        response = requests.get(url, params=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.warning(f"API call {function_name} failed: {e}")
        return []

# -------------------- Fetch Functions --------------------
def get_courses():
    return moodle_api_call("core_course_get_courses") or []

@st.cache_data(ttl=60)
def get_course_groupings_with_groups(course_id: int):
    """Get all groupings in course first"""
    resp = moodle_api_call("core_group_get_course_groupings", {"courseid": course_id})
    
    if isinstance(resp, dict) and "groupings" in resp:
        groupings = resp["groupings"]
    elif isinstance(resp, list):
        groupings = resp
    else:
        groupings = []
    
    valid_groupings = [g for g in groupings if isinstance(g, dict) and "id" in g and "name" in g]
    
    # Now get detailed groupings with groups using the groupingids
    if valid_groupings:
        grouping_ids = [g["id"] for g in valid_groupings]
        params = {"returngroups": 1}
        for i, gid in enumerate(grouping_ids):
            params[f"groupingids[{i}]"] = gid
        
        detailed = moodle_api_call("core_group_get_groupings", params)
        if isinstance(detailed, list):
            return detailed
    
    return valid_groupings

def get_course_groups(course_id: int):
    resp = moodle_api_call("core_group_get_course_groups", {"courseid": course_id})
    if isinstance(resp, dict) and "groups" in resp:
        groups = resp["groups"]
    elif isinstance(resp, list):
        groups = resp
    else:
        groups = []
    return [g for g in groups if isinstance(g, dict) and "id" in g and "name" in g]

@st.cache_data(ttl=60)
def get_group_members(group_id: int):
    """Get members of a specific group"""
    resp = moodle_api_call("core_group_get_group_members", {"groupid": group_id})
    
    if isinstance(resp, dict) and "exception" in resp:
        return []
    if isinstance(resp, dict) and "members" in resp:
        return resp["members"]
    elif isinstance(resp, list):
        return resp
    return []

def get_enrolled_students(course_id: int):
    resp = moodle_api_call("core_enrol_get_enrolled_users", {"courseid": course_id})
    if isinstance(resp, list):
        return resp
    return []

def get_assignments(course_id: int):
    data = moodle_api_call("mod_assign_get_assignments", {"courseids[0]": course_id})
    courses = data.get("courses", []) if isinstance(data, dict) else []
    if not courses:
        return []
    return courses[0].get("assignments", [])

def get_assignment_submissions(course_id: int, assignment_id: int):
    submissions_data = moodle_api_call("mod_assign_get_submissions", {"assignmentids[0]": assignment_id})
    assignments = submissions_data.get("assignments", []) if isinstance(submissions_data, dict) else []
    if not assignments:
        return []
    return assignments[0].get("submissions", [])

def merge_students_with_grades(students: list, assignment: dict):
    submission_data = get_assignment_submissions(assignment.get("course", 0), assignment.get("id", 0)) or []
    result = []
    due_date_ts = assignment.get("duedate", 0)

    for student in students:
        sub = next((s for s in submission_data if s.get("userid") == student.get("id")), None)
        grade = sub.get("grade") if sub else None
        status = sub.get("status") if sub else "missing"

        # Past-due missing = 0
        if (grade is None or grade == "") and due_date_ts > 0 and datetime.now().timestamp() > due_date_ts:
            grade = 0
            status = "missing_due"

        result.append({
            "id": student.get("id"),
            "name": student.get("fullname"),
            "grade": float(grade) if grade not in [None, ""] else None,
            "status": status
        })
    return result

# -------------------- Streamlit App --------------------
st.set_page_config(page_title="Moodle Dashboard", layout="wide")
st.title("📊 Moodle Student Performance Dashboard")

# -------------------- Sidebar Filters --------------------
st.sidebar.header("Filters")

# Courses
courses = get_courses()
if not courses:
    st.warning("No courses found.")
    st.stop()

course_options = {c.get("fullname", f"Course {c.get('id')}"): c.get("id") for c in courses}
course_name = st.sidebar.selectbox("Select Course", list(course_options.keys()))
course_id = course_options.get(course_name)

# Groupings / Classes - Get with detailed groups
groupings = get_course_groupings_with_groups(course_id)
grouping_options = {g.get("name", f"Grouping {g.get('id')}"): g.get("id") for g in groupings} if groupings else {"All Classes": None}
grouping_name = st.sidebar.selectbox("Select Class (Grouping)", list(grouping_options.keys()))
grouping_id = grouping_options.get(grouping_name)

# Groups - Get all groups in course
all_groups = get_course_groups(course_id)

# Filter groups by selected grouping
if grouping_id:
    # Get groups from the grouping
    selected_grouping = next((g for g in groupings if g.get("id") == grouping_id), None)
    if selected_grouping and "groups" in selected_grouping:
        filtered_groups = selected_grouping["groups"]
    else:
        filtered_groups = [g for g in all_groups if g.get("groupingid") == grouping_id]
else:
    filtered_groups = all_groups

group_options = {g.get("name", f"Group {g.get('id')}"): g.get("id") for g in filtered_groups} if filtered_groups else {}
group_name = st.sidebar.selectbox("Select Group (Optional)", ["All"] + list(group_options.keys()))
group_id = group_options.get(group_name)

# Assignments
assignments = get_assignments(course_id)
assignment_options = {a.get("name", f"Assignment {a.get('id')}"): a for a in assignments} if assignments else {}

# Make assignment filter optional
selected_assignment = None
if assignment_options:
    assignment_name = st.sidebar.selectbox("Select Assignment (Optional)", ["All Assignments"] + list(assignment_options.keys()))
    if assignment_name != "All Assignments":
        selected_assignment = assignment_options.get(assignment_name)
else:
    st.sidebar.info("No assignments found in this course.")

# Grade slider
min_grade = st.sidebar.slider("Show students below grade:", 0, 100, 50)

# -------------------- Fetch Students --------------------
students = get_enrolled_students(course_id) or []
if not students:
    st.warning("No students enrolled in this course.")
    st.stop()

# -------------------- Build Student-to-Grouping Mapping --------------------
student_to_grouping = {}

# Use grouping data with groups
for grouping in groupings:
    grouping_id_val = grouping.get("id")
    grouping_name_val = grouping.get("name")
    groups_in_grouping = grouping.get("groups", [])
    
    for group in groups_in_grouping:
        group_id_val = group.get("id")
        group_name_val = group.get("name")
        
        # Get members of this group
        members = get_group_members(group_id_val)
        for member in members:
            student_id = member.get("id")
            student_to_grouping[student_id] = {
                "grouping_id": grouping_id_val,
                "grouping_name": grouping_name_val,
                "group_name": group_name_val
            }

# -------------------- Filter by Class / Group --------------------
# Filter by grouping/class
if grouping_id:
    grouping_students = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id]
    students = [s for s in students if s.get("id") in grouping_students]

# Filter by group
if group_name != "All" and group_id:
    group_students = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id and 
                     (next((g for g in filtered_groups if g.get("id") == group_id), {}).get("id") == group_id)]
    students = [s for s in students if s.get("id") in group_students]

# -------------------- Merge Grades --------------------
if selected_assignment:
    # Single assignment selected
    submission_data = get_assignment_submissions(selected_assignment.get("course", 0), selected_assignment.get("id", 0)) or []
    student_grades = []
    due_date_ts = selected_assignment.get("duedate", 0)
    
    for student in students:
        sub = next((s for s in submission_data if s.get("userid") == student.get("id")), None)
        grade = sub.get("grade") if sub else None
        status = sub.get("status") if sub else "missing"
        
        # Show 0 if no grade and past due date, otherwise show "-"
        if grade is None or grade == "":
            if due_date_ts > 0 and datetime.now().timestamp() > due_date_ts:
                grade = 0
                status = "missing_due"
            else:
                grade = None  # Will display as "-" in dataframe
        else:
            grade = float(grade)
        
        student_grades.append({
            "id": student.get("id"),
            "name": student.get("fullname"),
            "grade": grade,
            "status": status
        })
    
    df = pd.DataFrame(student_grades)
    assignment_title = selected_assignment.get("name", "Unknown Assignment")
else:
    # All assignments - get grades for each student in each assignment
    student_grades = []
    
    for student in students:
        student_data = {
            "id": student.get("id"),
            "name": student.get("fullname"),
            "grades": {},
            "total_grade": None,
            "avg_grade": None
        }
        
        all_grades = []
        
        for assignment in assignments:
            submission_data = get_assignment_submissions(assignment.get("course", 0), assignment.get("id", 0)) or []
            sub = next((s for s in submission_data if s.get("userid") == student.get("id")), None)
            grade = sub.get("grade") if sub else None
            due_date_ts = assignment.get("duedate", 0)
            
            # Show 0 if no grade and past due date, otherwise show "-"
            if grade is None or grade == "":
                if due_date_ts > 0 and datetime.now().timestamp() > due_date_ts:
                    grade = 0
                else:
                    grade = None  # Will be shown as "-"
            else:
                grade = float(grade)
            
            assignment_name = assignment.get("name", f"Assignment {assignment.get('id')}")
            student_data["grades"][assignment_name] = grade
            
            # Only include in average if grade exists and is not 0 from past-due
            if grade not in [None, ""] and not (grade == 0 and due_date_ts > 0 and datetime.now().timestamp() > due_date_ts):
                all_grades.append(grade)
            elif grade not in [None, ""]:
                all_grades.append(grade)
        
        # Calculate average
        if all_grades:
            student_data["avg_grade"] = sum(all_grades) / len(all_grades)
        
        student_grades.append(student_data)
    
    # Create DataFrame
    if student_grades:
        df = pd.DataFrame([
            {
                "id": sg["id"],
                "name": sg["name"],
                **sg["grades"],
                "avg_grade": sg["avg_grade"]
            }
            for sg in student_grades
        ])
    else:
        df = pd.DataFrame()
    
    assignment_title = f"All Assignments ({len(assignments)} total)"

# Handle empty DataFrame gracefully
if df.empty:
    st.warning("No student grades to display yet.")
    st.stop()

# Add grouping information
df["grouping_id"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_id"))
df["grouping_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_name"))
df["group_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("group_name"))

# Format None values as "-"
def format_grade(val):
    if val is None or pd.isna(val):
        return "-"
    return val

# For single assignment, add status
if selected_assignment:
    df["status"] = "submitted"
    df_display = df.copy()
    # Format grade column
    df_display["grade"] = df_display["grade"].apply(format_grade)
    
    df_filtered = df[df["grade"].notna() & (df["grade"] < min_grade)]
    
    st.subheader(f"Students below grade {min_grade}")
    st.dataframe(df_filtered[["name", "grade", "status", "group_name", "grouping_name"]] if not df_filtered.empty else pd.DataFrame(columns=["name", "grade", "status", "group_name", "grouping_name"]))
    
    st.subheader(f"All Students - {assignment_title}")
    st.dataframe(df_display[["name", "grade", "status", "group_name", "grouping_name"]], use_container_width=True, hide_index=True)
else:
    # For all assignments, show average grade filter
    df_display = df.copy()
    
    # Format assignment columns
    for col in df.columns:
        if col not in ["id", "name", "avg_grade", "grouping_id", "grouping_name", "group_name"]:
            df_display[col] = df_display[col].apply(format_grade)
    
    if "avg_grade" in df.columns:
        df_filtered = df[df["avg_grade"].notna() & (df["avg_grade"] < min_grade)]
        
        st.subheader(f"Students with average grade below {min_grade}")
        if not df_filtered.empty:
            st.dataframe(df_filtered)
    
    st.subheader(f"All Students - {assignment_title}")
    st.dataframe(df_display, use_container_width=True, hide_index=True)

# -------------------- Class Comparison --------------------
if selected_assignment:
    avg_per_class = df.groupby("grouping_name")["grade"].mean().sort_values(ascending=False)
    st.subheader(f"Average Grade per Class for '{assignment_title}'")
else:
    avg_per_class = df.groupby("grouping_name")["avg_grade"].mean().sort_values(ascending=False)
    st.subheader(f"Average Grade per Class - {assignment_title}")

st.bar_chart(avg_per_class if not avg_per_class.empty else pd.Series([0], index=["No Class"]))

# -------------------- Performance Stats by Grouping --------------------
st.subheader("📊 Performance by Grouping")

if selected_assignment:
    grouping_stats = df.groupby("grouping_name").agg({
        "grade": ["mean", "median", "min", "max", "count"],
        "id": "count"
    }).round(2)
    grouping_stats.columns = ["Avg Grade", "Median Grade", "Min Grade", "Max Grade", "Submissions", "Total Students"]
else:
    grouping_stats = df.groupby("grouping_name").agg({
        "avg_grade": ["mean", "median", "min", "max"],
        "id": "count"
    }).round(2)
    grouping_stats.columns = ["Avg Grade", "Median Grade", "Min Grade", "Max Grade", "Total Students"]

st.dataframe(grouping_stats, use_container_width=True)

# -------------------- Top/Bottom performers --------------------
if selected_assignment:
    st.subheader("Top 5 Students")
    st.dataframe(df.sort_values(by="grade", ascending=False).head(5))

    st.subheader("Bottom 5 Students")
    st.dataframe(df.sort_values(by="grade", ascending=True).head(5))
else:
    st.subheader("Top 5 Students (by Average Grade)")
    st.dataframe(df.sort_values(by="avg_grade", ascending=False).head(5))

    st.subheader("Bottom 5 Students (by Average Grade)")
    st.dataframe(df.sort_values(by="avg_grade", ascending=True).head(5))

# -------------------- Debug Info --------------------
with st.expander("🔍 Debug Info"):
    st.subheader("Raw API Responses")
    
    groupings_raw = moodle_api_call("core_group_get_course_groupings", {"courseid": course_id})
    st.write("**Groupings Response:**")
    st.json(groupings_raw)
    
    if groupings:
        grouping_ids = [g.get("id") for g in groupings]
        params = {"returngroups": 1}
        for i, gid in enumerate(grouping_ids):
            params[f"groupingids[{i}]"] = gid
        
        detailed_groupings = moodle_api_call("core_group_get_groupings", params)
        st.write("**Detailed Groupings with Groups:**")
        st.json(detailed_groupings)
    
    groups_raw = moodle_api_call("core_group_get_course_groups", {"courseid": course_id})
    st.write("**Groups Response:**")
    st.json(groups_raw)
    
    st.subheader("Student-to-Grouping Mapping (sample)")
    sample_mapping = {str(k): v for k, v in list(student_to_grouping.items())[:10]}
    st.json(sample_mapping)