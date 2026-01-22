import os
from dotenv import load_dotenv
import requests
from datetime import datetime
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import plotly.express as px

# Import core modules
from api_service import fetch_all_courses, fetch_course_metadata, fetch_user_grades_batch
from data_processing import calculate_student_metrics

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
@st.cache_data(ttl=60)
def get_course_groupings_with_groups(course_id: int):
    """Get all groupings in course first"""
    resp = moodle_api_call("core_group_get_course_groupings", {"courseid": course_id})
    
    if isinstance(resp, dict) and "groupings" in resp:
        return resp.get("groupings", [])
    
    if isinstance(resp, list):
        groupings = resp
    else:
        groupings = []
    
    if not groupings:
        return []

    grouping_ids = [g.get("id") for g in groupings]
    params = {"returngroups": 1}
    for i, gid in enumerate(grouping_ids):
        params[f"groupingids[{i}]"] = gid
        
    detailed_resp = moodle_api_call("core_group_get_groupings", params)
    if isinstance(detailed_resp, list):
        return detailed_resp
    return groupings

@st.cache_data(ttl=60)
def get_course_groups(course_id: int):
    """Get all groups in course"""
    resp = moodle_api_call("core_group_get_course_groups", {"courseid": course_id})
    if isinstance(resp, list):
        return resp
    return []

def get_all_groups_with_grouping_id(groupings: list):
    """Extract all groups from groupings response and flatten list"""
    all_groups_with_grouping = []
    
    for grouping in groupings:
        grouping_id = grouping.get("id")
        groups_in_grouping = grouping.get("groups", [])
        
        for group in groups_in_grouping:
            group_copy = group.copy()
            group_copy["groupingid"] = grouping_id
            all_groups_with_grouping.append(group_copy)
    
    return all_groups_with_grouping

@st.cache_data(ttl=60)
def get_group_members(group_id: int):
    """Get members of a specific group - returns list of user IDs"""
    params = {"groupids[0]": group_id}
    resp = moodle_api_call("core_group_get_groups_members", params)
    
    def parse_members_response(response):
        if isinstance(response, dict) and "exception" in response:
            return None
        if isinstance(response, list):
            if response and isinstance(response[0], dict) and "userids" in response[0]:
                return response[0].get("userids", [])
            return response
        return None

    members = parse_members_response(resp)
    if members is not None:
        return members
        
    resp_fallback = moodle_api_call("core_group_get_group_members", params)
    members_fallback = parse_members_response(resp_fallback)
    
    if members_fallback is not None:
        return members_fallback
        
    return []

# -------------------- Streamlit App --------------------
st.set_page_config(page_title="Moodle Dashboard", layout="wide")
st.title("📊 Moodle Student Performance Dashboard")

# -------------------- Sidebar Filters --------------------
st.sidebar.header("Filters")

# Courses
courses_df = fetch_all_courses()

if not courses_df.empty:
    # Check if 'id' and 'fullname' exist
    if 'id' in courses_df.columns and 'fullname' in courses_df.columns:
        # Filter out course ID 1 (Site/Front Page/Guest course)
        courses_df = courses_df[courses_df['id'] != 1]
        
        courses_df['display'] = courses_df['id'].astype(str) + " - " + courses_df['fullname']
        course_options = courses_df['display'].tolist()
        
        if course_options:
            choice = st.sidebar.selectbox("Select Course", options=course_options)
            course_id = int(choice.split(" - ")[0])
        else:
            st.sidebar.warning("No courses found (excluding Site Home).")
            course_id = st.sidebar.number_input("Enter Course ID", value=2)
    else:
        st.sidebar.error("Could not parse course list. Check API permissions.")
        course_id = st.sidebar.number_input("Enter Course ID", value=2)
else:
    course_id = st.sidebar.number_input("Enter Course ID", value=2)

# --- Fetch metadata ---
with st.spinner("Fetching course data..."):
    users_raw, quizzes_raw, assigns_raw, submission_data, quiz_attempts_raw = fetch_course_metadata(course_id)

# Filter for students only (exclude guests with id=0 and teachers)
students = [
    u for u in users_raw 
    if u.get("id") != 0  # Exclude guest user (id=0)
    and u.get("username") not in ["guest", ""]  # Exclude guest username
    and not any(r.get('shortname') in ['teacher', 'editingteacher', 'manager'] for r in u.get('roles', []))
]

# Also exclude guest course (course=0)
assignments = [a for a in assigns_raw if a.get("course") != 0 and a.get("course") is not None]

if not students:
    st.warning("No students enrolled in this course.")
    st.stop()

# Groupings / Classes - Get with detailed groups
groupings = get_course_groupings_with_groups(course_id)

# Checkbox to enable filtering
use_filters = st.sidebar.checkbox("Filter by Class/Group", value=False)

grouping_id = None
group_id = None
selected_grouping = None
filtered_groups = []
group_name = "All"

if use_filters:
    grouping_options = {"All Classes": None}
    if groupings:
        grouping_options.update({g.get("name", f"Grouping {g.get('id')}"): g.get("id") for g in groupings})
    
    grouping_name = st.sidebar.selectbox("Select Class (Grouping)", list(grouping_options.keys()))
    grouping_id = grouping_options.get(grouping_name)
    
    all_groups = get_course_groups(course_id)
    all_groups_with_grouping = get_all_groups_with_grouping_id(groupings)
    
    if grouping_id:
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
    all_groups = get_course_groups(course_id)
    all_groups_with_grouping = get_all_groups_with_grouping_id(groupings)

# Assignments
assignment_options = {a.get("name", f"Assignment {a.get('id')}"): a for a in assignments} if assignments else {}

selected_assignment = None
use_assignment_filter = st.sidebar.checkbox("Filter by Single Assignment", value=False)
min_grade = None

if use_assignment_filter:
    if assignment_options:
        assignment_name = st.sidebar.selectbox("Select Assignment", list(assignment_options.keys()))
        selected_assignment = assignment_options.get(assignment_name)
        min_grade = st.sidebar.slider("Show students below grade:", 0, 100, 50)
    else:
        st.sidebar.info("No assignments found in this course.")

# -------------------- Build Student-to-Grouping Mapping --------------------
student_to_grouping = {}

# Initialize all students with "No Class" / "No Group"
for student in students:
    student_id = student.get("id")
    student_to_grouping[student_id] = {
        "grouping_id": None,
        "grouping_name": "No Class",
        "group_name": "No Group",
        "group_id": None
    }

# Override with actual grouping/group data
for grouping in groupings:
    grouping_id_map = grouping.get("id")
    grouping_name_map = grouping.get("name")
    groups_in_grouping = grouping.get("groups", [])
    
    for group in groups_in_grouping:
        group_id_map = group.get("id")
        group_name_map = group.get("name")
        member_ids = get_group_members(group_id_map)
        
        for member_id in member_ids:
            student_to_grouping[member_id] = {
                "grouping_id": grouping_id_map,
                "grouping_name": grouping_name_map,
                "group_name": group_name_map,
                "group_id": group_id_map
            }

# -------------------- Filter by Class / Group --------------------
filtered_students_raw = list(students)

if use_filters:
    if grouping_id:
        grouping_students = [sid for sid, info in student_to_grouping.items() if info.get("grouping_id") == grouping_id]
        filtered_students_raw = [s for s in filtered_students_raw if s.get("id") in grouping_students]
    
    if group_name != "All" and group_id:
        group_students = [sid for sid, info in student_to_grouping.items() if info.get("group_id") == group_id]
        filtered_students_raw = [s for s in filtered_students_raw if s.get("id") in group_students]

# -------------------- CALCULATE GRADES --------------------
weight_config = {}
for assign in assignments:
    key = f"assign_{assign['id']}"
    weight_config[key] = {
        'id': assign['id'],
        'name': assign['name'],
        'type': 'assign',
        'weight': 100.0,
        'duedate': assign.get('duedate', 0)
    }

with st.spinner("Calculating metrics..."):
    student_results, _ = calculate_student_metrics(
        filtered_students_raw, 
        weight_config, 
        course_id, 
        submission_data, 
        quiz_attempts_raw
    )

if student_results:
    df = pd.DataFrame(student_results)
else:
    df = pd.DataFrame()

assignment_title = f"All Assignments ({len(assignments)} assignments)"

# -------------------- Display Logic --------------------
if df.empty:
    with st.container(border=True):
        st.markdown("### ∅ No Data Found")
        st.markdown("No students matched your selected filters.")
        st.caption(f"Diagnostics: Total Enrolled: {len(students)} | Filtered List: {len(filtered_students_raw)}")
        if len(students) > 0 and len(filtered_students_raw) == 0:
            st.error("All students were filtered out! Check if 'Filter by Class/Group' is enabled with an empty class.")
else:
    # Add grouping information to DF
    df["id"] = df["User_ID"]
    df["grouping_id"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_id"))
    df["grouping_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("grouping_name"))
    df["group_id"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("group_id"))
    df["group_name"] = df["id"].map(lambda x: student_to_grouping.get(x, {}).get("group_name"))
    
    df["grouping_name"] = df["grouping_name"].fillna("No Class")
    df["group_name"] = df["group_name"].fillna("No Group")
    
    rename_map = {}
    display_cols = ['Name', 'Email', 'Final_Mark', 'grouping_name', 'group_name']
    
    for key, cfg in weight_config.items():
        raw_col = f"raw_{key}" 
        if raw_col in df.columns:
            new_name = cfg['name']
            rename_map[raw_col] = new_name
            display_cols.append(new_name)
    
    df_display = df.copy().rename(columns=rename_map)
    existing_cols = [c for c in display_cols if c in df_display.columns]
    
    if selected_assignment:
        assign_name = selected_assignment.get("name")
        if min_grade is not None and assign_name in df_display.columns:
            df_display[assign_name] = pd.to_numeric(df_display[assign_name], errors='coerce')
            df_display = df_display[df_display[assign_name].notna() & (df_display[assign_name] < min_grade)]
            st.subheader(f"Students below grade {min_grade} in '{assign_name}'")
        else:
            st.subheader(f"All Students - {assignment_title} (Filtered View: '{assign_name}')")
    else:
        st.subheader(f"All Students - {assignment_title}")

    st.dataframe(df_display[existing_cols], use_container_width=True, hide_index=True)

    # -------------------- Grouped Bar Chart --------------------
    st.divider()
    st.subheader("📊 Grade Distribution by Class and Assignment")
    
    if selected_assignment:
        assign_name = selected_assignment.get("name")
        if assign_name in df_display.columns:
            df_chart = df_display.copy()
            df_chart[assign_name] = pd.to_numeric(df_chart[assign_name], errors='coerce')
            
            # Group by class and calculate mean
            chart_data = df_chart.groupby("grouping_name")[assign_name].mean().reset_index()
            chart_data.columns = ["Class", "Average Grade"]
            
            # Create bar chart
            fig = px.bar(chart_data, x="Class", y="Average Grade", 
                        title=f"Average Grade per Class - {assign_name}",
                        color="Class", barmode="group",
                        labels={"Average Grade": "Grade"})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No data available for '{assign_name}'")
    else:
        # Create grouped bar chart for all assignments
        if not df_display.empty:
            # Prepare data for grouped bar chart
            chart_data_list = []
            
            for assign_key, assign_cfg in weight_config.items():
                assign_name = assign_cfg['name']
                if assign_name in df_display.columns:
                    # Group by class
                    grouped = df_display.groupby("grouping_name")[assign_name].apply(
                        lambda x: pd.to_numeric(x, errors='coerce').mean()
                    ).reset_index()
                    grouped.columns = ["Class", "Average Grade"]
                    grouped["Assignment"] = assign_name
                    chart_data_list.append(grouped)
            
            if chart_data_list:
                chart_data = pd.concat(chart_data_list, ignore_index=True)
                
                # Create grouped bar chart
                fig = px.bar(chart_data, x="Assignment", y="Average Grade", 
                            color="Class", barmode="group",
                            title="Average Grades by Assignment and Class",
                            labels={"Average Grade": "Grade"},
                            color_discrete_sequence=px.colors.qualitative.Plotly)
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No grade data available for chart")
        else:
            st.info("No data available")
    
    # -------------------- Performance Stats by Grouping --------------------
    st.divider()
    st.subheader("📊 Performance by Grouping")
    
    if selected_assignment:
        assign_name = selected_assignment.get("name")
        if assign_name in df_display.columns:
            df_grades = df_display.copy()
            df_grades[assign_name] = pd.to_numeric(df_grades[assign_name], errors='coerce')
            df_grades = df_grades[df_grades[assign_name].notna()]
            
            if not df_grades.empty:
                grouping_stats = df_grades.groupby("grouping_name").agg({
                    assign_name: ["mean", "median", "min", "max", "count"],
                }).round(2)
                grouping_stats.columns = ["Avg Grade", "Median Grade", "Min Grade", "Max Grade", "Submissions"]
                st.dataframe(grouping_stats, use_container_width=True)
            else:
                st.info("No stats available")
    else:
        if "Final_Mark" in df.columns:
            df_avg = df[df["Final_Mark"].notna()]
            if not df_avg.empty:
                grouping_stats = df_avg.groupby("grouping_name").agg({
                    "Final_Mark": ["mean", "median", "min", "max"],
                    "id": "count"
                }).round(2)
                grouping_stats.columns = ["Avg Mark", "Median Mark", "Min Mark", "Max Mark", "Total Students"]
                st.dataframe(grouping_stats, use_container_width=True)
            else:
                st.info("No stats available")
    
    # -------------------- Top/Bottom performers --------------------
    st.divider()
    st.subheader("🏆 Top/Bottom Performers")
    
    metric_col = "Final_Mark"
    if selected_assignment:
        assign_name = selected_assignment.get("name")
        if assign_name in df_display.columns:
            metric_col = assign_name
    
    if metric_col in df_display.columns:
        df_perf = df_display.copy()
        df_perf[metric_col] = pd.to_numeric(df_perf[metric_col], errors='coerce')
        df_perf = df_perf[df_perf[metric_col].notna()].sort_values(metric_col, ascending=False)
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Top 5 Students")
            st.dataframe(df_perf[["Name", metric_col, "group_name", "grouping_name"]].head(5), use_container_width=True, hide_index=True)
        
        with col2:
            st.subheader("Bottom 5 Students")
            st.dataframe(df_perf[["Name", metric_col, "group_name", "grouping_name"]].tail(5), use_container_width=True, hide_index=True)


# -------------------- Debug Info (Hidden by Default) --------------------
if st.sidebar.checkbox("Show Debug Logs", value=False):
    st.divider()
    
    st.subheader("📊 Data Source Summary")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Enrolled Students", len(students), "Excluding guests")
    
    with col2:
        st.metric("Students in Groupings", len(student_to_grouping), "With/without groups")
    
    with col3:
        df_count = len(df) if not df.empty else 0
        st.metric("Students in DataFrame", df_count, "After filtering")
    
    with col4:
        st.metric("Total Assignments", len(assignments), "Excluding guest course")
    
    st.divider()
    st.subheader("📋 Data Details")
    st.write(f"**Total Users (Raw):** {len(users_raw)}")
    st.write(f"**Students (Filtered - No guests, no teachers):** {len(students)}")
    st.write(f"**Total Assignments (Raw):** {len(assigns_raw)}")
    st.write(f"**Assignments (Filtered - No guest course):** {len(assignments)}")
    st.write(f"**Student-to-Grouping Mappings:** {len(student_to_grouping)}")
    
    # Show filtered out users
    st.write("**Filtered Out Users:**")
    guest_users = [u for u in users_raw if u.get("id") == 0 or u.get("username") == "guest"]
    teacher_users = [u for u in users_raw if any(r.get('shortname') in ['teacher', 'editingteacher', 'manager'] for r in u.get('roles', []))]
    st.write(f"  - Guest users: {len(guest_users)}")
    st.write(f"  - Teachers/Managers: {len(teacher_users)}")
    
    # Show filtered out assignments
    st.write("**Filtered Out Assignments:**")
    guest_assignments = [a for a in assigns_raw if a.get("course") == 1 or a.get("course") is None]
    st.write(f"  - Guest course (course_id=1) assignments: {len(guest_assignments)}")