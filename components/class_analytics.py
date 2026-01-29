import streamlit as st
import pandas as pd
import plotly.express as px
import moodle_client as mc
from data_processing import calculate_student_metrics

def get_course_groupings_with_groups_local(course_id: int):
    """Mirror of sd.py logic for fetching groupings"""
    # Use direct moodle_call from mc to ensure same parameters as sd.py
    import moodle_client
    resp = moodle_client.moodle_call("core_group_get_course_groupings", {"courseid": course_id})
    
    if isinstance(resp, dict) and "groupings" in resp:
        groupings = resp.get("groupings", [])
    elif isinstance(resp, list):
        groupings = resp
    else:
        groupings = []
    
    if not groupings:
        return []

    grouping_ids = [g.get("id") for g in groupings]
    params = {"returngroups": 1}
    for i, gid in enumerate(grouping_ids):
        params[f"groupingids[{i}]"] = gid
        
    detailed_resp = mc.moodle_call("core_group_get_groupings", params)
    if isinstance(detailed_resp, list):
        return detailed_resp
    return groupings

def get_group_members_local(group_id: int):
    """Mirror of sd.py logic for fetching group members - with added safety for record errors"""
    params = {"groupids[0]": group_id}
    # Use silent=True to suppress the Streamlit error display for known flaky Moodle records
    resp = mc.moodle_call("core_group_get_groups_members", params, silent=True)
    
    def parse_members_response(response):
        if not response or (isinstance(response, dict) and "exception" in response):
            return None
        if isinstance(response, list):
            if response and isinstance(response[0], dict) and "userids" in response[0]:
                return response[0].get("userids", [])
            return response
        return None

    members = parse_members_response(resp)
    if members is not None:
        return members
        
    resp_fallback = mc.moodle_call("core_group_get_group_members", params, silent=True)
    members_fallback = parse_members_response(resp_fallback)
    
    if members_fallback is not None:
        return members_fallback
        
    return []

def render_class_analytics(course_id, users_raw, quizzes_raw, assigns_raw, submission_data, quiz_attempts_raw):
    """
    Renders a 'mini sd.py' dashboard for class and group analysis.
    Uses EXACT logic and variable names from sd.py.
    """
    st.markdown("## Class & Group Analysis")
    
    # 1. FETCH GROUPINGS (Like sd.py line 180)
    groupings = get_course_groupings_with_groups_local(course_id)
    
    # 2. INTERNAL FILTERS (Like sd.py line 186-212)
    col1, col2, col3 = st.columns(3)
    with col1:
        grouping_options = {"All Classes": None}
        if groupings:
            grouping_options.update({g.get("name", f"Grouping {g.get('id')}"): g.get("id") for g in groupings})
        
        selected_class_name = st.selectbox("Select Class (Grouping)", list(grouping_options.keys()), key="ca_class_sel")
        selected_class_id = grouping_options.get(selected_class_name)

    with col2:
        filtered_groups = []
        if selected_class_id:
            selected_grouping = next((g for g in groupings if g.get("id") == selected_class_id), None)
            if selected_grouping and "groups" in selected_grouping:
                filtered_groups = selected_grouping["groups"]
        else:
            # Flattened list of groups like sd.py line 205
            for g in groupings:
                g_id = g.get("id")
                grs = g.get("groups", [])
                for gr in grs:
                    gr_copy = gr.copy()
                    gr_copy["groupingid"] = g_id
                    filtered_groups.append(gr_copy)

        group_options = {g.get("name", f"Group {g.get('id')}"): g.get("id") for g in filtered_groups}
        selected_group_name = st.selectbox("Select Group (Optional)", ["All"] + list(group_options.keys()), key="ca_group_sel")
        selected_group_id = group_options.get(selected_group_name)

    with col3:
        assessment_filter = st.selectbox("Assessment Type", ["All", "Assignments", "Quizzes"], key="ca_type_sel")

    # Filter students only (sd.py logic)
    staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator', 'staff', 'grader', 'admin', 'administrator']
    students = []
    for u in users_raw:
        if u.get("id") == 0 or u.get("username") in ["guest", ""]:
            continue
            
        u_roles = []
        for r in u.get('roles', []):
            if r.get('shortname'): u_roles.append(r['shortname'].lower())
            if r.get('name'): u_roles.append(r['name'].lower())
        
        if not any(role in staff_roles for role in u_roles):
            students.append(u)
    
    student_to_grouping_local = {}
    for student in students:
        student_id = student.get("id")
        student_to_grouping_local[student_id] = {
            "grouping_id": None,
            "grouping_name": "No Class",
            "group_name": "No Group",
            "group_id": None
        }

    for grouping in groupings:
        g_id = grouping.get("id")
        g_name = grouping.get("name")
        groups_in_grouping = grouping.get("groups", [])
        
        for group in groups_in_grouping:
            gr_id = group.get("id")
            gr_name = group.get("name")
            member_ids = get_group_members_local(gr_id)
            
            for m_id in member_ids:
                # Safety: handle both int and dict member_ids
                actual_uid = m_id.get('id') if isinstance(m_id, dict) else m_id
                if actual_uid in student_to_grouping_local:
                    student_to_grouping_local[actual_uid] = {
                        "grouping_id": g_id,
                        "grouping_name": g_name,
                        "group_name": gr_name,
                        "group_id": gr_id
                    }

    # 4. FILTER STUDENTS BY SELECTION
    filtered_students_raw = list(students)
    if selected_class_id:
        grouping_students = [sid for sid, info in student_to_grouping_local.items() if info.get("grouping_id") == selected_class_id]
        filtered_students_raw = [s for s in filtered_students_raw if s.get("id") in grouping_students]
    
    if selected_group_name != "All" and selected_group_id:
        group_students = [sid for sid, info in student_to_grouping_local.items() if info.get("group_id") == selected_group_id]
        filtered_students_raw = [s for s in filtered_students_raw if s.get("id") in group_students]

    # 5. CALCULATE GRADES (sd.py logic)
    weight_config_local = {}
    if assessment_filter in ["All", "Assignments"]:
        for a in assigns_raw:
            weight_config_local[f"assign_{a['id']}"] = {'id': a['id'], 'name': a['name'], 'type': 'assign', 'weight': 100.0}
    
    if assessment_filter in ["All", "Quizzes"]:
        for q in quizzes_raw:
            weight_config_local[f"quiz_{q['id']}"] = {'id': q['id'], 'name': q['name'], 'type': 'quiz', 'weight': 100.0}

    student_results, _ = calculate_student_metrics(filtered_students_raw, weight_config_local, course_id, submission_data, quiz_attempts_raw)
    
    if not student_results:
        st.warning("No students matched your selected filters.")
        return

    df = pd.DataFrame(student_results)
    df["id"] = df["User_ID"]
    df["grouping_name"] = df["id"].map(lambda x: student_to_grouping_local.get(x, {}).get("grouping_name", "No Class"))
    df["group_name"] = df["id"].map(lambda x: student_to_grouping_local.get(x, {}).get("group_name", "No Group"))

    # 6. RENDER VISUALIZATIONS (Mirror sd.py)
    st.divider()
    st.subheader("Average Grades by Assessment and Class")
    
    chart_data_list = []
    for key, cfg in weight_config_local.items():
        raw_col = f"raw_{key}"
        if raw_col in df.columns:
            # Group by class
            val_col = df[raw_col].apply(lambda x: pd.to_numeric(x, errors='coerce'))
            grouped = df.groupby("grouping_name")[raw_col].apply(lambda x: pd.to_numeric(x, errors='coerce').mean()).reset_index()
            grouped.columns = ["Class", "Average Grade"]
            grouped["Assessment"] = cfg['name']
            chart_data_list.append(grouped)
    
    if chart_data_list:
        chart_df = pd.concat(chart_data_list, ignore_index=True)
        fig = px.bar(chart_df, x="Assessment", y="Average Grade", color="Class", barmode="group", title="Average Grades by Assessment and Class")
        st.plotly_chart(fig, use_container_width=True)
        
        # New: Grade Distribution Box Plot
        st.subheader("Grade Distribution (Box Plot)")
        st.info("Box plots show the spread of marks: the box is the inner 50%, with the median line inside.")
        
        # We need raw data for the box plot, not averages
        box_data_list = []
        for key, cfg in weight_config_local.items():
            raw_col = f"raw_{key}"
            if raw_col in df.columns:
                temp_df = df[[raw_col, "grouping_name"]].copy()
                temp_df.columns = ["Mark", "Class"]
                temp_df["Assessment"] = cfg['name']
                box_data_list.append(temp_df)
        
        if box_data_list:
            box_df = pd.concat(box_data_list, ignore_index=True)
            fig_box = px.box(box_df, x="Assessment", y="Mark", color="Class", title="Grade Spread by Assessment")
            st.plotly_chart(fig_box, use_container_width=True)

    st.subheader("Performance Statistics")
    stats = df.groupby("grouping_name").agg({
        "Final_Mark": ["mean", "median", "min", "max", "count"]
    }).round(2)
    stats.columns = ["Avg Mark", "Median", "Min", "Max", "Count"]
    st.dataframe(stats.reset_index(), use_container_width=True, hide_index=True)

    st.subheader("Top Performers in Selection")
    top_5 = df.sort_values("Final_Mark", ascending=False).head(5)
    st.dataframe(top_5[["Name", "Final_Mark", "group_name", "grouping_name"]], use_container_width=True, hide_index=True)
