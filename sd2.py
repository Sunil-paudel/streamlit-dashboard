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
    """Get all groups in course - but we'll use groupings data instead for groupingid"""
    resp = moodle_api_call("core_group_get_course_groups", {"courseid": course_id})
    if isinstance(resp, dict) and "groups" in resp:
        groups = resp["groups"]
    elif isinstance(resp, list):
        groups = resp
    else:
        groups = []
    return [g for g in groups if isinstance(g, dict) and "id" in g and "name" in g]

def get_all_groups_with_grouping_id(groupings):
    """Extract all groups from groupings to get grouping_id mapping"""
    all_groups_with_grouping = []
    for grouping in groupings:
        grouping_id = grouping.get("id")
        groups_in_grouping = grouping.get("groups", [])
        
        for group in groups_in_grouping:
            group_copy = group.copy()
            group_copy["groupingid"] = grouping_id  # Add the grouping ID
            all_groups_with_grouping.append(group_copy)
    
    return all_groups_with_grouping

@st.cache_data(ttl=60)
def get_group_members(group_id: int):
    """Get members of a specific group - returns list of user IDs"""
    params = {"groupids[0]": group_id}
    
    # Try plural version first (standard)
    resp = moodle_api_call("core_group_get_groups_members", params)
    
    # Helper to parse response
    def parse_members_response(response):
        if isinstance(response, dict) and "exception" in response:
            return None
        if isinstance(response, list):
            # Check if it's a list of dicts with userids field
            if response and isinstance(response[0], dict) and "userids" in response[0]:
                return response[0].get("userids", [])
            # Otherwise it's a direct list of user IDs (older API/custom)
            return response
        return None

    members = parse_members_response(resp)
    if members is not None:
        return members
        
    # Fallback to singular version
    resp_fallback = moodle_api_call("core_group_get_group_members", params)
    members_fallback = parse_members_response(resp_fallback)
    
    if members_fallback is not None:
        return members_fallback
        
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

# Checkbox to enable filtering
use_filters = st.sidebar.checkbox("Filter by Class/Group", value=False)

grouping_id = None
group_id = None
selected_grouping = None
filtered_groups = []
group_name = "All"  # Default to All to prevent NameError


if use_filters:
    # Add "All Classes" option
    grouping_options = {"All Classes": None}
    if groupings:
        # Merge dictionaries, putting All Classes first
        grouping_options.update({g.get("name", f"Grouping {g.get('id')}"): g.get("id") for g in groupings})
    
    grouping_name = st.sidebar.selectbox("Select Class (Grouping)", list(grouping_options.keys()))
    grouping_id = grouping_options.get(grouping_name)
    
    # Groups - Get all groups with grouping IDs from groupings data
    all_groups = get_course_groups(course_id)
    all_groups_with_grouping = get_all_groups_with_grouping_id(groupings)
    
    # Filter groups by selected grouping
    if grouping_id:
        # Get groups from the grouping
        selected_grouping = next((g for g in groupings if g.get("id") == grouping_id), None)
        if selected_grouping and "groups" in selected_grouping:
            filtered_groups = selected_grouping["groups"]
        else:
            filtered_groups = [g for g in all_groups_with_grouping if g.get("groupingid") == grouping_id]
    else:
        filtered_groups = all_groups_with_grouping
    
    group_options = {g.get("name", f"Group {g.get('id')}"): g.get("id") for g in filtered_groups} if filtered_groups else {}
    group_name = st.sidebar.selectbox("Select Group (Optional)", ["All"] + list(group_options.keys()))
    group_id = group_options.get(group_name)
else:
    # Need all groups just for display references later if needed (though filtering is off)
    all_groups = get_course_groups(course_id)
    all_groups_with_grouping = get_all_groups_with_grouping_id(groupings)

# Assignments
assignments = get_assignments(course_id)
assignment_options = {a.get("name", f"Assignment {a.get('id')}"): a for a in assignments} if assignments else {}

# Make assignment filter optional
selected_assignment = None

# Checkbox to enable assignment filtering
use_assignment_filter = st.sidebar.checkbox("Filter by Single Assignment", value=False)

min_grade = None

if use_assignment_filter:
    if assignment_options:
        assignment_name = st.sidebar.selectbox("Select Assignment", list(assignment_options.keys()))
        selected_assignment = assignment_options.get(assignment_name)
        # Grade slider - Only show if assignment filter is active
        min_grade = st.sidebar.slider("Show students below grade:", 0, 100, 50)
    else:
        st.sidebar.info("No assignments found in this course.")

# -------------------- Fetch Students --------------------
students = get_enrolled_students(course_id) or []
if not students:
    st.warning("No students enrolled in this course.")
    st.stop()

# -------------------- Build Student-to-Grouping Mapping --------------------
student_to_grouping = {}

# Use the detailed groupings data that includes groups
for grouping in groupings:
    grouping_id_map = grouping.get("id")
    grouping_name_map = grouping.get("name")
    groups_in_grouping = grouping.get("groups", [])
    
    # For each group in this grouping
    for group in groups_in_grouping:
        group_id_map = group.get("id")
        group_name_map = group.get("name")
        
        # Get member IDs from this group
        member_ids = get_group_members(group_id_map)
        
        # Map each member ID to the grouping
        for member_id in member_ids:
            student_to_grouping[member_id] = {
                "grouping_id": grouping_id_map,
                "grouping_name": grouping_name_map,
                "group_name": group_name_map,
                "group_id": group_id_map
            }

# -------------------- Filter by Class / Group --------------------
# Start with all students
filtered_students = list(students)

# ONLY apply grouping/group filters if use_filters checkbox is enabled
if use_filters:
    # Filter by grouping/class
    if grouping_id:
        grouping_students = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id]
        filtered_students = [s for s in filtered_students if s.get("id") in grouping_students]
    
    # Filter by group
    if group_name != "All" and group_id:
        group_students = [sid for sid, info in student_to_grouping.items() if info.get("group_id") == group_id]
        filtered_students = [s for s in filtered_students if s.get("id") in group_students]

# -------------------- Merge Grades --------------------
# Use filtered students (after grouping and group filters)
if selected_assignment:
    # Single assignment selected
    submission_data = get_assignment_submissions(selected_assignment.get("course", 0), selected_assignment.get("id", 0)) or []
    student_grades = []
    due_date_ts = selected_assignment.get("duedate", 0)
    
    for student in filtered_students:
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
    # All assignments - Optimize fetching
    student_grades = []
    
    # 1. Pre-fetch all submissions to avoid N*M API calls
    # Cache key: assignment_id -> list of submissions
    submissions_cache = {}
    
    with st.spinner("Fetching grade data..."):
        for assignment in assignments:
            a_id = assignment.get("id")
            s_data = get_assignment_submissions(assignment.get("course", 0), a_id) or []
            submissions_cache[a_id] = s_data
            
    # 2. Build student rows
    for student in filtered_students:
        s_id = student.get("id")
        student_data = {
            "id": s_id,
            "name": student.get("fullname"),
            "grades": {},
            "total_grade": None,
            "avg_grade": None
        }
        
        all_grades = []
        
        for assignment in assignments:
            a_id = assignment.get("id")
            assignment_name = assignment.get("name", f"Assignment {a_id}")
            due_date_ts = assignment.get("duedate", 0)
            
            # Look up in cache
            submission_data = submissions_cache.get(a_id, [])
            sub = next((s for s in submission_data if s.get("userid") == s_id), None)
            
            grade = sub.get("grade") if sub else None
            
            # Show 0 if no grade and past due date, otherwise show "-"
            if grade is None or grade == "":
                if due_date_ts > 0 and datetime.now().timestamp() > due_date_ts:
                    grade = 0
                else:
                    grade = None  # Will be shown as "-"
            else:
                grade = float(grade)
            
            student_data["grades"][assignment_name] = grade
            
            # Only include in average if grade exists and is not 0 from past-due
            if grade is not None:
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
    
    assignment_title = f"All Assignments ({len(assignments)} assignments)"

# Handle empty DataFrame gracefully
if df.empty:
    with st.container(border=True):
        st.markdown("### ∅ No Data Found")
        st.markdown("No students matched your selected filters.")
        # Diagnostic info
        st.caption(f"Diagnostics: Total Enrolled: {len(students)} | Filtered List: {len(filtered_students)}")
        if len(students) > 0 and len(filtered_students) == 0:
            st.error("All students were filtered out! Check if 'Filter by Class/Group' is enabled with an empty class.")
else:
    # Add grouping information - handle missing groups/classes
    df["grouping_id"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_id"))
    df["grouping_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_name"))
    df["group_id"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("group_id"))
    df["group_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("group_name"))
    
    # Replace NaN/None with "No Class" and "No Group" for better readability
    df["grouping_name"] = df["grouping_name"].fillna("No Class")
    df["group_name"] = df["group_name"].fillna("No Group")
    
    if df.empty:
        st.warning("⚠️ No students found.")
        st.info("Check if students are enrolled in the course.")
    else:
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
            
            if min_grade is not None:
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
            
            if "avg_grade" in df.columns and min_grade is not None:
                df_filtered = df[df["avg_grade"].notna() & (df["avg_grade"] < min_grade)]
                
                st.subheader(f"Students with average grade below {min_grade}")
                if not df_filtered.empty:
                    st.dataframe(df_filtered)
            
            st.subheader(f"All Students - {assignment_title}")
            st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        # -------------------- Class Comparison --------------------
        st.divider()
        if selected_assignment:
            # Filter out None grades for chart
            df_grades = df[df["grade"].notna()]
            if not df_grades.empty:
                avg_per_class = df_grades.groupby("grouping_name")["grade"].mean().sort_values(ascending=False)
                st.subheader(f"📊 Average Grade per Class for '{assignment_title}'")
                st.bar_chart(avg_per_class)
            else:
                st.info("No grades available for chart")
        else:
            # For all assignments
            df_avg = df[df["avg_grade"].notna()]
            if not df_avg.empty:
                avg_per_class = df_avg.groupby("grouping_name")["avg_grade"].mean().sort_values(ascending=False)
                st.subheader(f"📊 Average Grade per Class - {assignment_title}")
                st.bar_chart(avg_per_class)
            else:
                st.info("No grades available for chart")
        
        # -------------------- Performance Stats by Grouping --------------------
        st.subheader("📊 Performance by Grouping")
        
        if selected_assignment:
            df_grades = df[df["grade"].notna()]
            if not df_grades.empty:
                grouping_stats = df_grades.groupby("grouping_name").agg({
                    "grade": ["mean", "median", "min", "max", "count"],
                    "id": "count"
                }).round(2)
                grouping_stats.columns = ["Avg Grade", "Median Grade", "Min Grade", "Max Grade", "Submissions", "Total Students"]
            else:
                grouping_stats = pd.DataFrame()
        else:
            df_avg = df[df["avg_grade"].notna()]
            if not df_avg.empty:
                grouping_stats = df_avg.groupby("grouping_name").agg({
                    "avg_grade": ["mean", "median", "min", "max"],
                    "id": "count"
                }).round(2)
                grouping_stats.columns = ["Avg Grade", "Median Grade", "Min Grade", "Max Grade", "Total Students"]
            else:
                grouping_stats = pd.DataFrame()
        
        if not grouping_stats.empty:
            st.dataframe(grouping_stats, use_container_width=True)
        else:
            st.info("No statistics available")
        
        # -------------------- Top/Bottom performers --------------------
        st.subheader("🏆 Top/Bottom Performers")
        
        if selected_assignment:
            df_grades = df[df["grade"].notna()].sort_values("grade", ascending=False)
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Top 5 Students")
                st.dataframe(df_grades[["name", "grade", "group_name", "grouping_name"]].head(5), use_container_width=True, hide_index=True)
            
            with col2:
                st.subheader("Bottom 5 Students")
                st.dataframe(df_grades[["name", "grade", "group_name", "grouping_name"]].tail(5), use_container_width=True, hide_index=True)
        else:
            df_avg = df[df["avg_grade"].notna()].sort_values("avg_grade", ascending=False)
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Top 5 Students (by Average)")
                st.dataframe(df_avg[["name", "avg_grade", "group_name", "grouping_name"]].head(5), use_container_width=True, hide_index=True)
            
            with col2:
                st.subheader("Bottom 5 Students (by Average)")
                st.dataframe(df_avg[["name", "avg_grade", "group_name", "grouping_name"]].tail(5), use_container_width=True, hide_index=True)

    # -------------------- Debug Info (Hidden by Default) --------------------
    if st.sidebar.checkbox("Show Debug Logs", value=False):
        st.divider()
        
        st.subheader("📊 Data Source Summary")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "Total Enrolled Students",
                len(students),
                "From: core_enrol_get_enrolled_users"
            )
        
        with col2:
            st.metric(
                "Students in Groupings",
                len(student_to_grouping),
                "From: core_group_get_groups_members"
            )
        
        with col3:
            df_count = len(df) if not df.empty else 0
            st.metric(
                "Students in DataFrame",
                df_count,
                "After filtering"
            )
        
        with col4:
            if selected_assignment and not df.empty:
                grades_count = len(df[df["grade"].notna()])
            else:
                grades_count = 0
            st.metric(
                "Students with Grades",
                grades_count,
                "Has submission"
            )
        
        st.divider()
        
        st.subheader("🔍 Where Each Number Comes From")
        
        st.write("**1. Total Enrolled Students** (API Call)")
        st.code("get_enrolled_users(course_id)\n→ Returns all students in the course", language="python")
        st.write(f"   Result: **{len(students)} students**")
        
        st.write("**2. Students in Groupings** (API Call)")
        st.code("""
    for each group:
        core_group_get_groups_members(groupid)
        → Returns userids in that group
        → Map to grouping
        """, language="python")
        st.write(f"   Result: **{len(student_to_grouping)} students** (only those assigned to groups)")
        
        if len(students) > len(student_to_grouping):
            st.warning(f"⚠️ **{len(students) - len(student_to_grouping)} students** are NOT assigned to any group")
        
        st.write("**3. Students in DataFrame** (After Filtering)")
        st.code("""
    if use_filters and grouping_id:
        df = df[df["grouping_id"] == selected_grouping_id]
    if use_filters and group_id:
        df = df[df["group_id"] == selected_group_id]
        """, language="python")
        st.write(f"   Result: **{len(df) if not df.empty else 0} students**")
        
        st.subheader("🔄 Filtering Flow (Step by Step)")
        
        st.write("**Step 1: Get all enrolled students**")
        st.code(f"students = get_enrolled_users(course_id)\n→ Result: {len(students)} students")
        
        st.write("**Step 2: Apply filters (only if 'Filter by Class/Group' is checked)**")
        if use_filters:
            st.code(f"use_filters = True")
            
            if grouping_id:
                grouping_name_debug = next((g.get("name") for g in groupings if g.get("id") == grouping_id), "Unknown")
                grouping_subset = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id]
                count_after_grouping = len([s for s in students if s.get("id") in grouping_subset])
                st.code(f"""# Grouping filter applied
    filtered_students = [s for s in students if s.id in grouping_students]
    → Result: {count_after_grouping} students in '{grouping_name_debug}'""")
            else:
                st.code(f"""# No specific grouping selected
    filtered_students = students
    → Result: {len(students)} students""")
            
            if group_name != "All" and group_id:
                group_subset = [sid for sid, info in student_to_grouping.items() if info.get("group_id") == group_id]
                count_after_group = len([s for s in students if s.get("id") in group_subset])
                st.code(f"""# Group filter applied
    filtered_students = [s for s in filtered_students if s.id in group_students]
    → Result: {count_after_group} students in '{group_name}'""")
            else:
                st.code(f"""# No group selected or 'All' selected
    filtered_students = filtered_students
    → Result: {len(filtered_students)} students""")
        else:
            st.code(f"""use_filters = False
    # NO FILTERING APPLIED
    filtered_students = list(students)
    → Result: {len(filtered_students)} students (all enrolled)""")
        
        st.write("**Step 3: Get grades for filtered students**")
        if selected_assignment:
            st.code(f"""for each student in filtered_students:
        get_assignment_submissions(assignment_id)
    → Result: DataFrame with {len(df)} students""")
        else:
            st.code(f"""for each student in filtered_students:
        get_assignment_submissions(all_assignments)
    → Result: DataFrame with {len(df)} students""")
        
        # Show the filter progression
        st.subheader("📊 Filter Progression Summary")
        
        if use_filters and grouping_id:
            grouping_subset = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id]
            count_step2 = len([s for s in students if s.get("id") in grouping_subset])
        else:
            count_step2 = len(students)
        
        if use_filters and group_name != "All" and group_id:
            group_subset = [sid for sid, info in student_to_grouping.items() if info.get("group_id") == group_id]
            count_step3 = len([s for s in students if s.get("id") in group_subset])
        else:
            count_step3 = count_step2
        
        progression_data = [
            {"Stage": "1. All Enrolled (Course Level)", "Count": len(students)},
            {"Stage": "2. After Grouping Filter", "Count": count_step2},
            {"Stage": "3. After Group Filter", "Count": count_step3},
            {"Stage": "4. In DataFrame", "Count": len(df) if not df.empty else 0},
        ]
        
        progression_df = pd.DataFrame(progression_data)
        st.dataframe(progression_df, use_container_width=True, hide_index=True)
    
        st.divider()
    
        # -------------------- Detailed Debug Table --------------------
        st.subheader("🕵️‍♂️ Detailed Debug: Student Inclusion Status")
        st.info("This table explains exactly why each student is Included or Excluded based on your current filters.")
        
        debug_data = []
        
        # helper to get selected names safely
        sel_grouping_name = next((g.get("name") for g in groupings if g.get("id") == grouping_id), "None") if grouping_id else "All"
        
        for s in students:
            s_id = s.get("id")
            s_name = s.get("fullname")
            
            # Get mapping info
            mapping = student_to_grouping.get(s_id, {})
            s_grouping_id = mapping.get("grouping_id")
            s_grouping_name = mapping.get("grouping_name", "No Class")
            s_group_id = mapping.get("group_id")
            s_group_name = mapping.get("group_name", "No Group")
            
            status = "✅ Included"
            reason = "Matches all filters"
            
            # Check exclusion logic only if filters are enabled
            if use_filters:
                # 1. Grouping Filter
                if grouping_id and s_grouping_id != grouping_id:
                    status = "❌ Excluded"
                    reason = f"Not in Class: {sel_grouping_name}"
                    
                # 2. Group Filter (only if passed grouping filter)
                elif group_name != "All" and group_id and s_group_id != group_id:
                    status = "❌ Excluded"
                    reason = f"Not in Group: {group_name}"
            
            debug_data.append({
                "ID": s_id,
                "Name": s_name,
                "Assigned Class": s_grouping_name,
                "Assigned Group": s_group_name,
                "Status": status,
                "Reason": reason
            })
            
        debug_df = pd.DataFrame(debug_data)
        st.dataframe(debug_df, use_container_width=True, hide_index=True)
    
        
        st.divider()
        
        # All enrolled students
        st.subheader("📋 All Enrolled Students (Raw)")
        enrolled_df = pd.DataFrame(students)
        st.dataframe(enrolled_df, use_container_width=True)
        
        st.divider()
        
        # Student-to-grouping mapping
        st.subheader("📍 Student-to-Grouping Mapping (Complete)")
        if student_to_grouping:
            mapping_list = []
            for student_id, grouping_info in student_to_grouping.items():
                student_name = next((s.get("fullname") for s in students if s.get("id") == student_id), "Unknown")
                mapping_list.append({
                    "Student ID": student_id,
                    "Student Name": student_name,
                    "Grouping ID": grouping_info.get("grouping_id"),
                    "Grouping Name": grouping_info.get("grouping_name"),
                    "Group Name": grouping_info.get("group_name")
                })
            mapping_df = pd.DataFrame(mapping_list)
            st.dataframe(mapping_df, use_container_width=True, hide_index=True)
            st.write(f"**Total mapped: {len(mapping_list)} students**")
        else:
            st.warning("No students mapped to groupings!")
        
        st.divider()
        
        # Dataframe content
        if not df.empty:
            st.subheader("📊 Dataframe with Grades (Complete)")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Dataframe is empty!")
        
        st.divider()
        
        # Groupings structure
        st.subheader("📚 Groupings Structure")
        if groupings:
            grouping_list = []
            for g in groupings:
                groups_in_grouping = g.get("groups", [])
                grouping_list.append({
                    "Grouping ID": g.get("id"),
                    "Grouping Name": g.get("name"),
                    "Course ID": g.get("courseid"),
                    "Number of Groups": len(groups_in_grouping),
                    "Groups": ", ".join([gr.get("name", f"Group {gr.get('id')}") for gr in groups_in_grouping])
                })
            grouping_df = pd.DataFrame(grouping_list)
            st.dataframe(grouping_df, use_container_width=True, hide_index=True)
        
        st.divider()
        
        # Groups structure
        st.subheader("📍 Groups Structure (with Grouping IDs from Groupings)")
        if all_groups_with_grouping:
            group_list = []
            for g in all_groups_with_grouping:
                group_list.append({
                    "Group ID": g.get("id"),
                    "Group Name": g.get("name"),
                    "Grouping ID": g.get("groupingid"),
                    "Course ID": g.get("courseid"),
                    "Description": g.get("description", "")
                })
            group_df = pd.DataFrame(group_list)
            st.dataframe(group_df, use_container_width=True, hide_index=True)
        
        st.divider()
        
        # Group members
        st.subheader("👥 Group Members (for each group)")
        for group in all_groups:
            group_id_debug = group.get("id")
            group_name_debug = group.get("name")
            grouping_id_debug = group.get("groupingid")
            members = get_group_members(group_id_debug)
            
            st.write(f"**Group: {group_name_debug} (ID: {group_id_debug}) → Grouping ID: {grouping_id_debug}**")
            
            if members:
                members_list = []
                for member in members:
                    members_list.append({
                        "Student ID": member.get("id"),
                        "Full Name": member.get("fullname"),
                        "Email": member.get("email"),
                        "Group ID": group_id_debug,
                        "Group Name": group_name_debug
                    })
                members_df = pd.DataFrame(members_list)
                st.dataframe(members_df, use_container_width=True, hide_index=True)
            else:
                st.warning(f"  ⚠️ No members found in this group")
        
        st.divider()
        
        # Grouping to Group to Student relationships
        st.subheader("🔗 Grouping → Group → Student Relationships")
        
        relationship_data = []
        for grouping_rel in groupings:
            grouping_id_rel = grouping_rel.get("id")
            grouping_name_rel = grouping_rel.get("name")
            groups_in_grouping_rel = grouping_rel.get("groups", [])
            
            st.write(f"**Grouping: {grouping_name_rel} (ID: {grouping_id_rel})**")
            
            for group_rel in groups_in_grouping_rel:
                group_id_rel = group_rel.get("id")
                group_name_rel = group_rel.get("name")
                
                # Get member IDs from this group
                member_ids_rel = get_group_members(group_id_rel)
                
                st.write(f"  └─ Group: {group_name_rel} (ID: {group_id_rel})")
                
                if member_ids_rel:
                    st.write(f"     Found {len(member_ids_rel)} members")
                    for member_id_rel in member_ids_rel:
                        # Find student info
                        student_info = next((s for s in students if s.get("id") == member_id_rel), None)
                        if student_info:
                            st.write(f"      └─ Student: {student_info.get('fullname')} (ID: {member_id_rel})")
                            relationship_data.append({
                                "Grouping ID": grouping_id_rel,
                                "Grouping Name": grouping_name_rel,
                                "Group ID": group_id_rel,
                                "Group Name": group_name_rel,
                                "Student ID": member_id_rel,
                                "Student Name": student_info.get("fullname")
                            })
                        else:
                            st.write(f"      └─ User ID: {member_id_rel} (not in enrolled students)")
                else:
                    st.write(f"      ⚠️ No members in this group")
        
        st.divider()
        
        if relationship_data:
            st.subheader("📊 Complete Relationship Matrix")
            relationship_df = pd.DataFrame(relationship_data)
            st.dataframe(relationship_df, use_container_width=True, hide_index=True)
            st.write(f"**Total relationships found: {len(relationship_data)}**")
        else:
            st.error("❌ **No relationships found!** This could mean:")
            st.write("- 1. Students are not assigned to any groups")
            st.write("- 2. Groups exist but are empty")
            st.write("- 3. The API permissions don't allow viewing group members")
            st.write("")
            st.info("**Alternative: Assign students manually in Moodle**")
            st.write("Go to: Course → Participants → Groups → Add members to groups")
        
        st.divider()
        
        # Assignments and submissions
        st.subheader("📋 Assignments and Submissions")
        for assignment_debug in assignments:
            assignment_id_debug = assignment_debug.get("id")
            assignment_name_debug = assignment_debug.get("name")
            due_date = assignment_debug.get("duedate")
            
            st.write(f"**Assignment: {assignment_name_debug} (ID: {assignment_id_debug})**")
            st.write(f"  - Due Date: {datetime.fromtimestamp(due_date) if due_date else 'No due date'}")
            
            submissions = get_assignment_submissions(assignment_debug.get("course", 0), assignment_id_debug)
            if submissions:
                submissions_df = pd.DataFrame(submissions)
                st.dataframe(submissions_df, use_container_width=True, hide_index=True)
            else:
                st.warning(f"  No submissions found for this assignment")
        
        st.divider()

        # Grouping and Group Reference Table
        st.subheader("📑 Grouping and Group Reference Table")
        if groupings:
            reference_list = []
            for g in groupings:
                g_id = g.get("id")
                g_name = g.get("name")
                groups_in_g = g.get("groups", [])
                
                if groups_in_g:
                    for gr in groups_in_g:
                        reference_list.append({
                            "Grouping ID": g_id,
                            "Grouping Name": g_name,
                            "Group ID": gr.get("id"),
                            "Group Name": gr.get("name")
                        })
                else:
                    reference_list.append({
                        "Grouping ID": g_id,
                        "Grouping Name": g_name,
                        "Group ID": None,
                        "Group Name": "No Groups"
                    })
            
            reference_df = pd.DataFrame(reference_list)
            st.dataframe(reference_df, use_container_width=True, hide_index=True)

        st.divider()
        
        # Raw API Responses
        st.subheader("🔌 Raw API Responses")
        
        groupings_raw = moodle_api_call("core_group_get_course_groupings", {"courseid": course_id})
        st.write("**Groupings Response:**")
        st.json(groupings_raw)
        
        if groupings:
            grouping_ids_raw = [g.get("id") for g in groupings]
            params_raw = {"returngroups": 1}
            for i, gid_raw in enumerate(grouping_ids_raw):
                params_raw[f"groupingids[{i}]"] = gid_raw
            
            detailed_groupings = moodle_api_call("core_group_get_groupings", params_raw)
            st.write("**Detailed Groupings with Groups:**")
            st.json(detailed_groupings)
        
        groups_raw = moodle_api_call("core_group_get_course_groups", {"courseid": course_id})
        st.write("**Groups Response:**")
        st.json(groups_raw)



"""
Arguments
groupids (Required)
        

General structure

list of ( 
int   //Group ID
)
XML-RPC (PHP structure)

[groupids] =>
    Array 
        (
        [0] => int
        )
REST (POST parameters)

groupids[0]= int



Response
General structure

list of ( 
object {
groupid int   //group record id
userids list of ( 
int   //user id
)} 
)
XML-RPC (PHP structure)


    Array 
        (
        [0] =>
            Array 
                (
                [groupid] => int                
                [userids] =>
                    Array 
                        (
                        [0] => int
                        )                
                )
        )
REST

<?xml version="1.0" encoding="UTF-8" ?>
<RESPONSE>
    <MULTIPLE>
        <SINGLE>
            <KEY name="groupid">
                <VALUE>int</VALUE>
            </KEY>
            <KEY name="userids">
                <MULTIPLE>
                    <VALUE>int</VALUE>
                </MULTIPLE>
            </KEY>
        </SINGLE>
    </MULTIPLE>
</RESPONSE>



Error message

REST

<?xml version="1.0" encoding="UTF-8"?>
<EXCEPTION class="invalid_parameter_exception">
    <MESSAGE>Invalid parameter value detected</MESSAGE>
    <DEBUGINFO></DEBUGINFO>
</EXCEPTION>


Restricted to logged-in users
Yes

Callable from AJAX
No


Returns all groupings in specified course.


Arguments
courseid (Required)
        id of course

General structure

int   //id of course

XML-RPC (PHP structure)

[courseid] => int
REST (POST parameters)

courseid= int



Response
General structure

list of ( 
object {
id int   //grouping record id
courseid int   //id of course
name string   //multilang compatible name, course unique
description string   //grouping description text
descriptionformat int   //description format (1 = HTML, 0 = MOODLE, 2 = PLAIN, or 4 = MARKDOWN)
idnumber string   //id number
} 
)
XML-RPC (PHP structure)


    Array 
        (
        [0] =>
            Array 
                (
                [id] => int                
                [courseid] => int                
                [name] => string                
                [description] => string                
                [descriptionformat] => int                
                [idnumber] => string                
                )
        )
REST

<?xml version="1.0" encoding="UTF-8" ?>
<RESPONSE>
    <MULTIPLE>
        <SINGLE>
            <KEY name="id">
                <VALUE>int</VALUE>
            </KEY>
            <KEY name="courseid">
                <VALUE>int</VALUE>
            </KEY>
            <KEY name="name">
                <VALUE>string</VALUE>
            </KEY>
            <KEY name="description">
                <VALUE>string</VALUE>
            </KEY>
            <KEY name="descriptionformat">
                <VALUE>int</VALUE>
            </KEY>
            <KEY name="idnumber">
                <VALUE>string</VALUE>
            </KEY>
        </SINGLE>
    </MULTIPLE>
</RESPONSE>



Error message

REST

<?xml version="1.0" encoding="UTF-8"?>
<EXCEPTION class="invalid_parameter_exception">
    <MESSAGE>Invalid parameter value detected</MESSAGE>
    <DEBUGINFO></DEBUGINFO>
</EXCEPTION>


Restricted to logged-in users
Yes

Callable from AJAX
No

core_group_get_course_groups 










































core_group_get_course_user_groups 































































