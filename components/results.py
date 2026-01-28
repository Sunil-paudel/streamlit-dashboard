import streamlit as st
import pandas as pd
from datetime import datetime
from api_service import sync_grade_to_moodle

def render_detailed_results(df, total_target, weight_config, course_id):
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

            # Add individual assessment as percentage
            for k, cfg in weight_config.items():
                r = u.get(f"raw_{k}", 0.0)
                # Use the 'max' column computed during metrics calculation
                m = u.get(f"max_{k}", cfg['weight']) or cfg['weight']
                perc = (r / m * 100) if m > 0 else 0
                row[f"{cfg['name']} (%)"] = round(perc, 2)
            
            # Add adjustment reason column
            row["Adjustment Reason"] = ""

            detailed_list.append(row)

        detailed_df = pd.DataFrame(detailed_list)
        
        # Make assessment columns editable
        editable_cols = [col for col in detailed_df.columns if col.endswith(" (%)")]
        disabled_cols = [col for col in detailed_df.columns if col not in editable_cols and col != "Adjustment Reason"]

        # Display editable table
        edited_df = st.data_editor(
            detailed_df,
            disabled=disabled_cols,
            hide_index=True,
            use_container_width=True,
            key="detailed_results_editor"
        )

        # Detect changes
        changes_detected = []
        for idx, (orig_row, edit_row) in enumerate(zip(detailed_df.iterrows(), edited_df.iterrows())):
            orig_data = orig_row[1]
            edit_data = edit_row[1]
            
            for col in editable_cols:
                if abs(orig_data[col] - edit_data[col]) > 0.01:  # Tolerance for floating point
                    # Extract assessment key from column name
                    assessment_name = col.replace(" (%)", "")
                    # Find the corresponding key in weight_config
                    item_key = None
                    for k, cfg in weight_config.items():
                        if cfg['name'] == assessment_name:
                            item_key = k
                            break
                    
                    if item_key:
                        item_cmid = weight_config[item_key].get('cmid')
                        
                        changes_detected.append({
                            'user_id': edit_data['User_ID'],
                            'name': edit_data['Name'],
                            'item_key': item_key,
                            'item_name': assessment_name,
                            'item_type': weight_config[item_key]['type'],
                            'item_id': weight_config[item_key]['id'],
                            'item_cmid': item_cmid,
                            'old_perc': orig_data[col],
                            'new_perc': edit_data[col],
                            'max_points': weight_config[item_key]['weight'],
                            'reason': edit_data.get('Adjustment Reason', '')
                        })

        # Review Pending Changes
        if changes_detected:
            with st.expander(f"Review Pending Changes ({len(changes_detected)} modifications)", expanded=True):

                for change in changes_detected:
                    st.markdown(f"""
                    **{change['name']}** - {change['item_name']}:
                    - Old: {change['old_perc']:.2f}% -> New: {change['new_perc']:.2f}%

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
                            st.write(f"---\n**Processing: {change['name']} - {change['item_name']}**")
                            
                            # Convert percentage back to raw score
                            new_raw = (change['new_perc'] / 100) * change['max_points']
                            st.write(f"Percentage: {change['new_perc']:.2f}% -> Raw score: {new_raw:.2f}/{change['max_points']}")

                            
                            # Sync both assignments and quizzes
                            success, message = sync_grade_to_moodle(
                                course_id=course_id,
                                user_id=change['user_id'],
                                item_id=change['item_id'],
                                item_type=change['item_type'],
                                grade_value=new_raw,
                                item_cmid=change.get('item_cmid')
                            )
                            
                            if success:
                                st.success(f"{change['name']} - {change['item_name']}: {message}")

                                success_count += 1
                            else:
                                st.error(f"{change['name']} - {change['item_name']}: {message}")

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
