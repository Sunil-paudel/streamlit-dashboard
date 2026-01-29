import streamlit as st
import pandas as pd
from datetime import datetime
from api_service import sync_grade_to_moodle
from redis_client import get_redis, PREFIX_DRAFT

redis = get_redis()

def render_detailed_results(df, total_target, weight_config, course_id, group_mapping=None, metadata=None, moodle_baseline=None):
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
        baseline_source = moodle_baseline if moodle_baseline else df.to_dict('records')
        for u in baseline_source:
            uid = str(u.get('User_ID'))
            moodle_vals[uid] = {}
            for k in weight_config.keys():
                moodle_vals[uid][k] = float(u.get(f"raw_{k}", 0.0))

        # Detect changes and UPDATE Redis Draft
        changes_detected = []
        new_redis_drafts = {} 
        
        df_edit = edited_df.set_index('User_ID')
        
        # --- PASS 1: Detect ACTIVE USER EDITS (Prioritize these) ---
        # If a user explicitly changes a cell, that "Intent" propagates to the group.
        for u_id_int in df_edit.index:
            u_id = str(u_id_int)
            edit_row = df_edit.loc[u_id_int]
            
            for col in editable_cols:
                val_edit = float(edit_row[col])
                
                # Identify item_key
                item_key = None
                for k, header in header_mapping.items():
                    if header == col:
                        item_key = k
                        break
                
                if item_key:
                    # Current Draft Value (or Moodle Baseline if no draft)
                    current_draft_val = existing_drafts.get(u_id, {}).get(item_key)
                    if current_draft_val is None:
                        current_draft_val = moodle_vals.get(u_id, {}).get(item_key, 0.0)
                    
                    # Check if ACTIVE EDIT (Differs from what was loaded)
                    if abs(val_edit - float(current_draft_val)) > 0.001:
                        # User just typed this! Trigger Propagation.
                        
                        # 1. Identify Group Members
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
                                # Filter staff
                                staff_roles = ['teacher', 'editingteacher', 'manager', 'coursecreator', 'staff', 'grader', 'admin', 'administrator']
                                student_ids_in_metadata = set()
                                if metadata and 'users' in metadata:
                                    for user_obj in metadata['users']:
                                        u_roles = []
                                        for r in user_obj.get('roles', []):
                                            if r.get('shortname'): u_roles.append(r['shortname'].lower())
                                            if r.get('name'): u_roles.append(r['name'].lower())
                                        if not any(role in staff_roles for role in u_roles):
                                            student_ids_in_metadata.add(str(user_obj['id']))

                                target_uids = list(set([str(u_id)] + [str(m) for m in members if str(m) in student_ids_in_metadata]))

                        # 2. Apply Active Edit to All Targets
                        for target_id in target_uids:
                            target_id_str = str(target_id)
                            if target_id_str not in new_redis_drafts: new_redis_drafts[target_id_str] = {}
                            new_redis_drafts[target_id_str][item_key] = val_edit

        # --- PASS 2: Detect PASSIVE/EXISTING DRAFTS (Fill gaps) ---
        # If a value differs from Moodle but wasn't "Just Edited" (i.e. it's a preserved draft), keep it.
        # BUT do NOT overwrite anything set in Pass 1.
        for u_id_int in df_edit.index:
            u_id = str(u_id_int)
            edit_row = df_edit.loc[u_id_int]
            
            for col in editable_cols:
                val_edit = float(edit_row[col])
                
                # Identify item_key
                item_key = None
                for k, header in header_mapping.items():
                    if header == col:
                        item_key = k
                        break
                
                if item_key:
                    orig_moodle_val = moodle_vals.get(u_id, {}).get(item_key, 0.0)
                    
                    # If this row differs from moodle, it's a pending change.
                    if abs(val_edit - orig_moodle_val) > 0.001:
                        # Add to draft only if PASS 1 didn't already touch this cell
                        if u_id not in new_redis_drafts or item_key not in new_redis_drafts[u_id]:
                            if u_id not in new_redis_drafts: new_redis_drafts[u_id] = {}
                            new_redis_drafts[u_id][item_key] = val_edit

        # --- GENERATE CHANGES DETECTED LIST (From the final consolidated drafts) ---
        for u_id, items in new_redis_drafts.items():
            for item_key, val_new in items.items():
                # Find display info
                orig_val = moodle_vals.get(u_id, {}).get(item_key, 0.0)
                
                # Find Name logic (from ID)
                name_disp = uid_to_name.get(u_id, f"User {u_id}")
                # Try to get from dataframe if possible for better match? 
                # (uid might not be in edit_df if filtered, but usually is)
                
                changes_detected.append({
                    'user_id': int(u_id),
                    'name': name_disp,
                    'item_key': item_key,
                    'item_name': weight_config[item_key]['name'],
                    'item_type': weight_config[item_key]['type'],
                    'item_id': weight_config[item_key]['id'],
                    'item_cmid': weight_config[item_key].get('cmid'),
                    'old_raw': orig_val,
                    'new_raw': val_new,
                    'max_points': weight_config[item_key].get('grademax', 100.0),
                    'reason': '' # Reason tracking is complex with propagation, simplifying to empty for auto-prop
                })

        # Save current diffs back to Redis
        if new_redis_drafts != existing_drafts:
            # Only rerun if the save was SUCCESSFUL (True)
            # If Redis is down (False), we just proceed to render the UI with current in-memory changes
            if redis.set_json(draft_key, new_redis_drafts):
                st.rerun()

        # Review Pending Changes
        if changes_detected:
            # Check for group assignments and add warning
            has_group_assign = any(weight_config.get(c['item_key'], {}).get('teamsubmission') == 1 for c in changes_detected)
            if has_group_assign:
                st.info("💡 **Note**: Some changes are for **Group Assignments**. Updates will automatically be applied to all group members.")

            with st.expander(f"Review Pending Changes ({len(changes_detected)} modifications)", expanded=True):
                st.write("This list includes **all unsaved drafts**, including edits from previous sessions.")
                st.write("You can deselect students or override individual marks below before syncing.")
                
                col_clear, _ = st.columns([1, 4])
                with col_clear:
                    if st.button("🗑️ Clear All Drafts", type="secondary"):
                        redis.delete(draft_key)
                        st.rerun()
                
                # Convert to editable DF for selective sync
                review_list = []
                for c in changes_detected:
                    review_list.append({
                        "Sync": True,
                        "Name": c['name'],
                        "Assessment": c['item_name'],
                        "Old Mark": round(c['old_raw'], 2),
                        "New Mark": round(c['new_raw'], 2),
                        "Max": c['max_points'],
                        "Reason": c['reason'],
                        "user_id": c['user_id'], # hidden ID
                        "item_key": c['item_key'] # hidden key
                    })
                
                review_df = pd.DataFrame(review_list)
                edited_review = st.data_editor(
                    review_df,
                    column_config={
                        "Sync": st.column_config.CheckboxColumn("Sync?", default=True),
                        "New Mark": st.column_config.NumberColumn("New Mark", min_value=0.0, step=0.1),
                        "Name": st.column_config.TextColumn("Name", disabled=True),
                        "Assessment": st.column_config.TextColumn("Assessment", disabled=True),
                        "Old Mark": st.column_config.TextColumn("Old Mark", disabled=True),
                        "Max": st.column_config.TextColumn("Max", disabled=True),
                        "Reason": st.column_config.TextColumn("Reason", disabled=True),
                        "user_id": None, # hide
                        "item_key": None  # hide
                    },
                    hide_index=True,
                    use_container_width=True,
                    key="sync_editor"
                )

                # Filter finalized list based on checkboxes and overrides
                final_sync_list = []
                overridden_drafts = {} # user_id -> {item_key: val}
                
                for _, row in edited_review.iterrows():
                    if row["Sync"]:
                        # Find the original change object and update it
                        orig_c = next((c for c in changes_detected if c['user_id'] == row['user_id'] and c['item_key'] == row['item_key']), None)
                        if orig_c:
                            orig_c['new_raw'] = float(row['New Mark'])
                            final_sync_list.append(orig_c)
                            
                            # Record for draft update
                            uid_s = str(row['user_id'])
                            if uid_s not in overridden_drafts: overridden_drafts[uid_s] = {}
                            overridden_drafts[uid_s][row['item_key']] = float(row['New Mark'])

                # Update Redis drafts if user did manual overrides in the sync table
                if overridden_drafts:
                    current_redis = redis.get_json(draft_key) or {}
                    changed_any = False
                    for u, items in overridden_drafts.items():
                        for k, v in items.items():
                            if u in current_redis and k in current_redis[u] and abs(current_redis[u][k] - v) > 0.001:
                                current_redis[u][k] = v
                                changed_any = True
                    if changed_any:
                        redis.set_json(draft_key, current_redis)

            # Push to Moodle button
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.warning(f"Warning: This will update grades for {len(final_sync_list)} selected students. Make sure you have reviewed all changes above.")

            with col2:
                if st.button("Push to Moodle", type="primary", disabled=len(final_sync_list) == 0):
                    success_count = 0
                    fail_count = 0
                    
                    with st.spinner("Syncing grades to Moodle..."):
                        for change in final_sync_list:
                            # 2. Sync for this specific user individually
                            success, message = sync_grade_to_moodle(
                                course_id=course_id,
                                user_id=change['user_id'],
                                item_id=change['item_id'],
                                item_type=change['item_type'],
                                grade_value=change['new_raw'],
                                item_cmid=change.get('item_cmid'),
                                apply_to_all=False 
                            )
                            
                            if success:
                                st.success(f"✅ {change['name']}: {message}")
                                success_count += 1
                                
                                # Clear local draft for this specific user/item
                                current_drafts = redis.get_json(draft_key) or {}
                                item_k = change['item_key']
                                uid_str = str(change['user_id'])
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
