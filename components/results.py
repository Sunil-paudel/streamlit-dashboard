import streamlit as st
import pandas as pd
from datetime import datetime
from api_service import sync_grade_to_moodle
from redis_client import get_redis, PREFIX_DRAFT

redis = get_redis()

def render_detailed_results(df, total_target, weight_config, course_id, group_mapping=None, metadata=None):
    """
    Renders the Detailed Results tab content and handles grade sync.
    """
    st.markdown("### Student Detailed Performance (Editable)")
    st.info("Edit assessment scores below and click 'Push to Moodle' to sync changes.")


    if df.empty:
        st.info("No data.")
    else:
        # --- Redis Draft Loading ---
        draft_key = f"{PREFIX_DRAFT}{course_id}"
        existing_drafts = redis.get_json(draft_key) or {} # user_id -> {item_key: val}
        
        # Pre-resolve student names for propagation lookups
        uid_to_name = {str(u['id']): u['fullname'] for u in metadata.get('users', [])} if metadata else {}
        
        # Pre-calculate unique headers to prevent stacking if names are identical
        # ... [rest of logic] ...
        header_mapping = {} # key -> unique_header
        name_counts = {}
        for k, cfg in weight_config.items():
            name = cfg['name']
            name_counts[name] = name_counts.get(name, 0) + 1
        
        for k, cfg in weight_config.items():
            m = cfg.get('grademax', 100.0) or 100.0
            base_header = f"{cfg['name']} (Raw / {m})"
            if name_counts.get(cfg['name'], 0) > 1:
                # Append ID to make it unique if there's a name collision
                header_mapping[k] = f"{cfg['name']} [ID:{cfg['id']}] (Raw / {m})"
            else:
                header_mapping[k] = base_header

        # Pre-process group names for fast lookup
        group_id_to_name = {str(g['id']): g['name'] for g in group_mapping.get('groups', [])} if group_mapping else {}
        # Pre-process grouping names
        group_to_grouping = {}
        if group_mapping and 'groupings' in group_mapping:
            for gping in group_mapping['groupings']:
                gp_name = gping.get('name', 'N/A')
                for grp in gping.get('groups', []):
                    group_to_grouping[str(grp['id'])] = gp_name

        detailed_list = []
        for _, u in df.iterrows():
            u_id = str(u['User_ID'])
            
            # Resolve Group and Class (Grouping)
            u_groups = group_mapping['user_to_groups'].get(u_id, []) if group_mapping else []
            g_names = [group_id_to_name.get(str(gid), "Unknown") for gid in u_groups]
            gp_names = list(set([group_to_grouping.get(str(gid), "No Class") for gid in u_groups]))
            
            row = {
                "User_ID": u['User_ID'],
                "Name": u['Name'],
                "Class": ", ".join(gp_names) if gp_names else "No Class",
                "Group": ", ".join(g_names) if g_names else "No Group",
                "Email": u['Email'],
                "Score": f"{u['Final_Mark']:.2f} / {total_target:.2f}",
                "Clicks": int(u.get('Clicks', 0)),
                "Dwell_Hours": round(u.get('Dwell_Hours', 0), 2),
                "Days_Since_Last": int(u.get('Days_Since_Last', 0)),
                "Status": u.get('Status', 'N/A'),
            }

            # Add individual assessment as Raw Score
            for k, cfg in weight_config.items():
                # Check if we have a persistent draft in Redis
                if u_id in existing_drafts and k in existing_drafts[u_id]:
                    r = existing_drafts[u_id][k]
                else:
                    r = u.get(f"raw_{k}", 0.0)
                
                col_header = header_mapping[k]
                row[col_header] = float(r)
                row[f"max_val_{k}"] = cfg.get('grademax', 100.0) or 100.0
            
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

        # Build original moodle values map for change detection relative to REAL Moodle data
        # Mapping: user_id -> {item_key: raw_val}
        moodle_vals = {}
        for _, u in df.iterrows():
            uid = str(u['User_ID'])
            moodle_vals[uid] = {}
            for k in weight_config.keys():
                moodle_vals[uid][k] = float(u.get(f"raw_{k}", 0.0))

        # Detect changes and UPDATE Redis Draft
        changes_detected = []
        new_redis_drafts = {} # We'll rebuild this to capture all current diffs
        
        df_edit = edited_df.set_index('User_ID')
        
        for u_id_int in df_edit.index:
            u_id = str(u_id_int)
            edit_row = df_edit.loc[u_id_int]
            
            for col in editable_cols:
                val_edit = float(edit_row[col])
                
                # Correctly identify item_key using header_mapping
                item_key = None
                for k, header in header_mapping.items():
                    if header == col:
                        item_key = k
                        break
                
                if item_key:
                    # Compare with ORIGINAL MOODLE DATA
                    orig_moodle_val = moodle_vals.get(u_id, {}).get(item_key, 0.0)
                    
                    if abs(orig_moodle_val - val_edit) > 0.001:
                        # 1. Identify all target users for this change
                        target_uids = [u_id]
                        is_group_assign = (weight_config[item_key]['type'] == 'assign' and 
                                          weight_config[item_key].get('teamsubmission') == 1)
                        
                        if is_group_assign and group_mapping:
                            grouping_id = weight_config[item_key].get('groupingid')
                            user_groups = group_mapping['user_to_groups'].get(u_id, [])
                            target_group_id = None
                            if grouping_id and grouping_id > 0:
                                for grouping in group_mapping['groupings']:
                                    if grouping['id'] == grouping_id:
                                        grs_in_g = [g['id'] for g in grouping.get('groups', [])]
                                        common = list(set(user_groups) & set(grs_in_g))
                                        if common: target_group_id = common[0]
                                        break
                            else:
                                if user_groups: target_group_id = user_groups[0]
                            
                            if target_group_id:
                                members = group_mapping['group_membership'].get(str(target_group_id), [])
                                target_uids = list(set([str(u_id)] + [str(m) for m in members]))

                        # 2. Record the change for ALL target users
                        for target_id in target_uids:
                            target_id_str = str(target_id)
                            # Update Redis Drafts
                            if target_id_str not in new_redis_drafts: new_redis_drafts[target_id_str] = {}
                            new_redis_drafts[target_id_str][item_key] = val_edit
                            
                            # Update Review Table (ensure uniqueness)
                            if not any(c['user_id'] == int(target_id_str) and c['item_key'] == item_key for c in changes_detected):
                                t_name = edit_row['Name'] if target_id_str == u_id else uid_to_name.get(target_id_str, f"User {target_id_str}")
                                t_orig = moodle_vals.get(target_id_str, {}).get(item_key, 0.0)
                                
                                changes_detected.append({
                                    'user_id': int(target_id_str),
                                    'name': t_name,
                                    'item_key': item_key,
                                    'item_name': weight_config[item_key]['name'],
                                    'item_type': weight_config[item_key]['type'],
                                    'item_id': weight_config[item_key]['id'],
                                    'item_cmid': weight_config[item_key].get('cmid'),
                                    'old_raw': t_orig,
                                    'new_raw': val_edit,
                                    'max_points': weight_config[item_key].get('grademax', 100.0),
                                    'reason': edit_row.get('Adjustment Reason', '')
                                })

        # Save current diffs back to Redis
        if new_redis_drafts != existing_drafts:
            redis.set_json(draft_key, new_redis_drafts)
            st.rerun()

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
                        synced_users_this_batch = set()
                        
                        for change in changes_detected:
                            if change['user_id'] in synced_users_this_batch:
                                continue

                            # 1. Check for group assignment propagation
                            is_group_assign = (change['item_type'] == 'assign' and 
                                              weight_config.get(change['item_key'], {}).get('teamsubmission') == 1)
                            
                            target_user_ids = [change['user_id']]
                            apply_all_flag = False

                            if is_group_assign and group_mapping:
                                apply_all_flag = True
                                # Find other members just to clear their local drafts later
                                uid_str = str(change['user_id'])
                                grouping_id = weight_config[change['item_key']].get('groupingid')
                                user_groups = group_mapping['user_to_groups'].get(uid_str, [])
                                
                                target_group_id = None
                                if grouping_id and grouping_id > 0:
                                    for grouping in group_mapping['groupings']:
                                        if grouping['id'] == grouping_id:
                                            groups_in_grouping = [g['id'] for g in grouping.get('groups', [])]
                                            common = list(set(user_groups) & set(groups_in_grouping))
                                            if common: target_group_id = common[0]
                                            break
                                else:
                                    if user_groups: target_group_id = user_groups[0]
                                
                                if target_group_id:
                                    members = group_mapping['group_membership'].get(str(target_group_id), [])
                                    target_user_ids = list(set(target_user_ids + members))
                                    st.write(f"👥 **Group Detected**: Syncing and propagating to {len(target_user_ids)} members...")

                            # 2. Sync for the primary user (with apply_to_all if group assignment)
                            success, message = sync_grade_to_moodle(
                                course_id=course_id,
                                user_id=change['user_id'],
                                item_id=change['item_id'],
                                item_type=change['item_type'],
                                grade_value=change['new_raw'],
                                item_cmid=change.get('item_cmid'),
                                apply_to_all=apply_all_flag
                            )
                            
                            if success:
                                st.success(f"✅ {change['name']}: {message}")
                                success_count += 1
                                
                                # 3. Mark all affected users as synced and clear their local drafts
                                current_drafts = redis.get_json(draft_key) or {}
                                item_k = change['item_key']
                                
                                for uid in target_user_ids:
                                    synced_users_this_batch.add(uid)
                                    uid_str = str(uid)
                                    if uid_str in current_drafts and item_k in current_drafts[uid_str]:
                                        del current_drafts[uid_str][item_k]
                                        if not current_drafts[uid_str]: del current_drafts[uid_str]
                                
                                redis.set_json(draft_key, current_drafts)
                            else:
                                st.error(f"❌ {change['name']}: {message}")
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
