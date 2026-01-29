import streamlit as st
import pandas as pd
from datetime import datetime
from api_service import sync_grade_to_moodle

def render_detailed_results(df, total_target, weight_config, course_id, group_mapping=None):
    """
    Renders the Detailed Results tab content and handles grade sync.
    """
    st.markdown("### Student Detailed Performance (Editable)")
    st.info("Edit assessment scores below and click 'Push to Moodle' to sync changes.")


    if df.empty:
        st.info("No data.")
    else:
        # Initialize session state for tracking changes
        if 'grade_changes' not in st.session_state:
            st.session_state.grade_changes = {}
        
        detailed_list = []
        for _, u in df.iterrows():
            row = {
                "User_ID": u['User_ID'],
                "Name": u['Name'],
                "Email": u['Email'],
                "Score": f"{u['Final_Mark']:.2f} / {total_target:.2f}",
                "Clicks": int(u.get('Clicks', 0)),
                "Dwell_Hours": round(u.get('Dwell_Hours', 0), 2),
                "Days_Since_Last": int(u.get('Days_Since_Last', 0)),
                "Status": u.get('Status', 'N/A'),
            }

            # Add individual assessment as Raw Score
            for k, cfg in weight_config.items():
                r = u.get(f"raw_{k}", 0.0)
                m = u.get(f"max_{k}", cfg.get('grademax', 100.0)) or 100.0
                # Include the Moodle Max in the column header for clarity
                col_header = f"{cfg['name']} (Raw / {m})"
                row[col_header] = float(r)
                row[f"max_val_{k}"] = m
            
            # Add adjustment reason column
            row["Adjustment Reason"] = ""

            detailed_list.append(row)

        detailed_df = pd.DataFrame(detailed_list)
        
        # Make assessment columns editable
        editable_cols = [col for col in detailed_df.columns if " (Raw / " in col]
        disabled_cols = [col for col in detailed_df.columns if col not in editable_cols and col != "Adjustment Reason"]

        # Display editable table
        edited_df = st.data_editor(
            detailed_df,
            disabled=disabled_cols,
            hide_index=True,
            use_container_width=True,
            key="detailed_results_editor"
        )

        # Detect changes using User_ID as key to avoid mismatch if sorted
        changes_detected = []
        
        # Set User_ID as index for stable comparison
        df_orig = detailed_df.set_index('User_ID')
        df_edit = edited_df.set_index('User_ID')
        
        for u_id in df_orig.index:
            orig_row = df_orig.loc[u_id]
            edit_row = df_edit.loc[u_id]
            
            for col in editable_cols:
                # Compare as floats to be safe
                val_orig = float(orig_row[col])
                val_edit = float(edit_row[col])
                
                if abs(val_orig - val_edit) > 0.001:
                    # Extract assessment key from column name
                    assessment_name = col.split(" (Raw / ")[0]
                    item_key = None
                    for k, cfg in weight_config.items():
                        if cfg['name'] == assessment_name:
                            item_key = k
                            break
                    
                    if item_key:
                        changes_detected.append({
                            'user_id': int(u_id),
                            'name': edit_row['Name'],
                            'item_key': item_key,
                            'item_name': assessment_name,
                            'item_type': weight_config[item_key]['type'],
                            'item_id': weight_config[item_key]['id'],
                            'item_cmid': weight_config[item_key].get('cmid'),
                            'old_raw': val_orig,
                            'new_raw': val_edit,
                            'max_points': weight_config[item_key].get('grademax', 100.0),
                            'reason': edit_row.get('Adjustment Reason', '')
                        })

        # Review Pending Changes
        if changes_detected:
            # Check for group assignments and add warning
            has_group_assign = any(weight_config.get(c['item_key'], {}).get('teamsubmission') == 1 for c in changes_detected)
            if has_group_assign:
                st.info("💡 **Note**: Some changes are for **Group Assignments**. Updates will automatically be applied to all group members.")

            with st.expander(f"Review Pending Changes ({len(changes_detected)} modifications)", expanded=True):
                # Show a summary table
                review_df = pd.DataFrame(changes_detected)
                st.table(review_df[['name', 'item_name', 'old_raw', 'new_raw', 'max_points']])

                for change in changes_detected:
                    st.markdown(f"""
                    **{change['name']}** - {change['item_name']}:
                    - Old: {change['old_raw']:.2f} -> New: {change['new_raw']:.2f} (Max: {change['max_points']})
                    - Reason: {change['reason'] if change['reason'] else '_No reason provided_'}
                    """)
            
            # Push to Moodle button
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.warning("Warning: This will update grades in your Moodle Gradebook. Make sure you have reviewed all changes above.")

            with col2:
                if st.button("Push to Moodle", type="primary"):
                    success_count = 0
                    fail_count = 0
                    
                    with st.spinner("Syncing grades to Moodle..."):
                        for change in changes_detected:
                            # 1. Identify target users (self + group members if applicable)
                            target_user_ids = [change['user_id']]
                            is_group_assign = (change['item_type'] == 'assign' and 
                                              weight_config.get(change['item_key'], {}).get('teamsubmission') == 1)
                            
                            if is_group_assign and group_mapping:
                                grouping_id = weight_config[change['item_key']].get('groupingid')
                                user_groups = group_mapping['user_to_groups'].get(change['user_id'], [])
                                
                                target_group_id = None
                                if grouping_id and grouping_id > 0:
                                    # Find the group in the correct grouping
                                    for grouping in group_mapping['groupings']:
                                        if grouping['id'] == grouping_id:
                                            groups_in_grouping = [g['id'] for g in grouping.get('groups', [])]
                                            common = list(set(user_groups) & set(groups_in_grouping))
                                            if common: target_group_id = common[0]
                                            break
                                else:
                                    if user_groups: target_group_id = user_groups[0]
                                
                                if target_group_id:
                                    members = group_mapping['group_membership'].get(target_group_id, [])
                                    target_user_ids = list(set(target_user_ids + members))
                                    st.write(f"👥 **Group Detected**: Propagating grade to {len(target_user_ids)} members (Group ID: {target_group_id})")

                            # 2. Sync for each target user
                            for uid in target_user_ids:
                                user_name = change['name'] if uid == change['user_id'] else f"User ID {uid} (Group Member)"
                                
                                success, message = sync_grade_to_moodle(
                                    course_id=course_id,
                                    user_id=uid,
                                    item_id=change['item_id'],
                                    item_type=change['item_type'],
                                    grade_value=change['new_raw'],
                                    item_cmid=change.get('item_cmid')
                                )
                                
                                if success:
                                    st.success(f"✅ {user_name}: {message}")
                                    success_count += 1
                                else:
                                    st.error(f"❌ {user_name}: {message}")
                                    fail_count += 1
                    
                    st.info(f"Sync complete: {success_count} successful, {fail_count} failed/skipped")
                    
                    # Clear cache to refresh data
                    if success_count > 0:
                        st.cache_data.clear()
                        st.info("Refresh the page to see updated grades from Moodle.")


        # CSV download
        st.markdown("---")
        csv = edited_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Detailed Results CSV (with adjustments)",

            data=csv,
            file_name="student_detailed_results_edited.csv",
            mime="text/csv"
        )
