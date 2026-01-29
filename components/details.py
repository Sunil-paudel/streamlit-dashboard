import streamlit as st
import pandas as pd

def render_student_details(df, total_target, weight_config, log_window_days, group_mapping=None):
    """
    Renders the Student Details tab content.
    """
    if not df.empty:
        lookup = st.selectbox("Select Student", df['Name'].sort_values().unique())
        
        if lookup:
            subset = df[df['Name']==lookup]
            if not subset.empty:
                s = subset.iloc[0]
                u_id = int(s['User_ID'])
                st.markdown(f"### Student: {s['Name']}")
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Final Mark", f"{s['Final_Mark']:.2f} / {total_target:.2f}")
                col2.metric("Engagement", f"{s['Engagement_Score']:.2f}%")
                col3.metric("Clicks / Week", f"{s.get('Clicks_Per_Week', 0.0):.2f}")
                col4.metric(f"Total Clicks ({log_window_days}d)", f"{int(s.get('Clicks', 0))}")
                col5.metric(f"Dwell Hours ({log_window_days}d)", f"{s.get('Dwell_Hours', 0.0):.2f}h")
                st.markdown(f"**Last Active:** {int(s['Days_Since_Last'])} days ago | **Risk Score:** {s['Risk_Score']:.2f} ({s['Risk_Category']})")

                # Get student's group IDs
                u_groups = group_mapping['user_to_groups'].get(u_id, []) if group_mapping else []

                breakdown = []
                for k,v in weight_config.items():
                    pts = s.get(f"pts_{k}", 0)
                    is_overdue = s.get(f"overdue_{k}", False)
                    is_inprogress = s.get(f"inprogress_{k}", False)
                    is_viewed = s.get(f"viewed_{k}", False)
                    
                    if pts > 0:
                        status_icon = "Complete"
                    elif is_overdue:
                        status_icon = "Overdue"
                    elif is_inprogress or is_viewed:
                        status_icon = "Active"
                    else:
                        status_icon = "Pending"

                    raw_val = s.get(f"raw_{k}", 0)
                    max_val = s.get(f"max_{k}", v.get('grademax', 100.0))
                    
                    # Group Info for this assessment
                    group_name = "N/A"
                    grouping_name = "N/A"
                    if v.get('teamsubmission') == 1 and group_mapping:
                        grouping_id = v.get('groupingid')
                        # Find the group name
                        if grouping_id and grouping_id > 0:
                            # Search in groupings
                            for grouping in group_mapping['groupings']:
                                if grouping['id'] == grouping_id:
                                    grouping_name = grouping.get('name', 'N/A')
                                    # Identify which of user's groups is in this grouping
                                    grs_in_g = [gr['id'] for gr in grouping.get('groups', [])]
                                    common = list(set(u_groups) & set(grs_in_g))
                                    if common:
                                        # Find group name
                                        gr_id = common[0]
                                        for gr in group_mapping['groups']:
                                            if gr['id'] == gr_id:
                                                group_name = gr.get('name', 'N/A')
                                                break
                                    break
                    
                    breakdown.append({
                        "Assessment": v['name'],
                        "Due Date": s.get(f"due_{k}", "N/A"),
                        "Raw Score": f"{raw_val} / {max_val}",
                        "Percentage": f"{(raw_val/max_val*100):.1f}%" if max_val > 0 else "0%",
                        "Group": group_name,
                        "Status": status_icon
                    })
                st.table(pd.DataFrame(breakdown))
            else:
                st.warning("Student data not found.")
        else:
            st.info("No students available to display.")
    else:
         st.info("No data available.")
