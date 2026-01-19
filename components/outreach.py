import streamlit as st
import pandas as pd
from utils import send_automated_email

def render_outreach(df, weight_config, coord_email):
    """
    Renders the Outreach tab content.
    """
    st.markdown("### ✉️ Student Outreach & Email Alerts")

    if not df.empty and 'Risk_Score' in df.columns:
        # -------- Filter Controls --------
        st.markdown("#### 🎯 Filter Controls")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("**Risk-Based Filtering**")
            t_val = st.slider("Risk Score Threshold:", 0, 100, 50)
            cat_filter = st.multiselect("Include Categories:", ['🔴 Critical', '🟡 Warning', '🟢 Safe'], default=['🔴 Critical', '🟡 Warning'])
        
        with col2:
            st.markdown("**Assessment-Based Filtering**")
            # Build list of assessment names
            item_options = [f"{cfg['name']}" for key, cfg in weight_config.items()]
            selected_items = st.multiselect(
                "Filter by Specific Items:",
                options=item_options,
                help="Select specific quizzes or assignments to target students who scored below the threshold."
            )
            score_threshold = st.slider(
                "Score Threshold (%):",
                min_value=0,
                max_value=100,
                value=40,
                step=10,
                help="Students scoring below this % in ANY selected item will be included."
            )
        
        # Logic toggle
        narrow_by_risk = st.checkbox(
            "Narrow by Activity/Risk? (AND logic)",
            value=False,
            help="If checked, students must match BOTH risk filters AND item filters. If unchecked, students matching EITHER will be included."
        )
        
        # -------- Apply Filters --------
        # Risk-based mask
        risk_mask = (df['Risk_Score'] >= t_val) | (df['Risk_Category'].isin(cat_filter))
        
        # Item-based mask
        item_mask = pd.Series([False] * len(df), index=df.index)
        if selected_items:
            for idx, row in df.iterrows():
                for key, cfg in weight_config.items():
                    if cfg['name'] in selected_items:
                        # Check if student scored below threshold
                        raw = row.get(f"raw_{key}", 0)
                        max_pts = row.get(f"max_{key}", cfg['weight']) or cfg['weight']
                        perc = (raw / max_pts * 100) if max_pts > 0 else 0
                        if perc < score_threshold:
                            item_mask[idx] = True
                            break
        
        # Combine masks based on logic
        if selected_items and narrow_by_risk:
            # AND logic: must match both
            final_mask = risk_mask & item_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)} **AND** Scoring < {score_threshold:.0f}% in: {', '.join(selected_items)}"
        elif selected_items:
            # OR logic: match either
            final_mask = risk_mask | item_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)} **OR** Scoring < {score_threshold:.0f}% in: {', '.join(selected_items)}"
        else:
            # Only risk-based
            final_mask = risk_mask
            filter_desc = f"Score ≥ {t_val} OR Categories: {', '.join(cat_filter)}"
        
        # Build the base columns
        base_cols = ['Name', 'Email', 'Risk_Score', 'Risk_Category', 'Assignments_Gap', 'Quizzes_Gap', 'Clicks', 'Days_Since_Last', 'Status']
        preview_targets = df[final_mask][base_cols].copy()
        
        # Add individual assessment scores as percentage columns
        for key, cfg in weight_config.items():
            col_name = f"{cfg['name']} (%)"
            preview_targets[col_name] = df[final_mask].apply(
                lambda row: round((row.get(f"raw_{key}", 0) / (row.get(f"max_{key}", cfg['weight']) or cfg['weight']) * 100) if (row.get(f"max_{key}", cfg['weight']) or cfg['weight']) > 0 else 0, 2),
                axis=1
            )

        st.markdown(f"### Target List ({filter_desc})")
        if not preview_targets.empty:
            # Add selection column
            preview_targets.insert(0, "Select", True)
            
            # Interactive editor
            edited_df = st.data_editor(
                preview_targets,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Email?",
                        help="Select students to send emails to",
                        default=True,
                    )
                },
                disabled=["Name", "Email", "Risk_Score", "Assignments_Gap", "Quizzes_Gap", "Clicks", "Days_Since_Last", "Status"],
                hide_index=True,
                width=None # Stretch disabled or default
            )
            
            # Filter for selected students
            final_targets = edited_df[edited_df['Select']]
        else:
            st.info("No students exceed the selected Risk Score threshold.")
            final_targets = pd.DataFrame()

        # -------- Custom Email Template --------
        st.markdown("---")
        st.subheader("📝 Customize Email Template")
        st.info("💡 **Available Placeholders:** `{Name}`, `{Risk_Category}`, `{Assignments_Gap}`, `{Quizzes_Gap}`, `{Days_Since_Last}`")
        
        default_template = """Hi {Name},

We’re reaching out to check in and offer support, as our learning system indicates that you may benefit from reviewing your current course engagement.

Here’s a brief overview of your current progress:

• Risk category: {Risk_Category}
• Pending assignments: {Assignments_Gap}
• Pending quizzes: {Quizzes_Gap}
• Course activity: Below class average
• Last active: {Days_Since_Last} days ago

These indicators help us identify students who may need additional support. If you’ve been facing any challenges—academic, technical, or personal—please know that help is available.

We encourage you to log in, review your upcoming tasks, and reach out to your course coordinator or student support services if you need assistance. Taking early action can make a meaningful difference.

Kind regards,
Student Support Team"""

        email_template = st.text_area("Email Message Body", value=default_template, height=300)

        # -------- Student Emails --------
        if st.button(f"📨 Email Selected Students ({len(final_targets)})"):
            if final_targets.empty:
                st.warning("No students selected.")
            else:
                sent_count = 0
                for _, r in final_targets.iterrows():
                    # Format individual email using placeholders
                    try:
                        body = email_template.format(
                            Name=r['Name'],
                            Risk_Category=r['Risk_Category'],
                            Assignments_Gap=r['Assignments_Gap'],
                            Quizzes_Gap=r['Quizzes_Gap'],
                            Days_Since_Last=int(r['Days_Since_Last'])
                        )
                    except KeyError as e:
                        st.error(f"❌ Placeholder error: {e}. Please check your template.")
                        break

                    # Preview email
                    with st.expander(f"Preview Email to {r['Name']}", expanded=False):
                        st.code(body)

                    # Send email
                    success = send_automated_email(r['Email'], "A quick check-in about your course progress", body)
                    if success:
                        st.success(f"✅ Email sent to {r['Name']}")
                        sent_count += 1
                    else:
                        st.error(f"❌ Failed to send email to {r['Name']}")

                st.info(f"Total emails successfully sent: {sent_count}/{len(final_targets)}")

        # -------- Coordinator Summary --------
        st.markdown("---")
        st.subheader("Coordinator Summary")

        if st.button("📋 Send Coordinator Summary"):
            body = f"""Coordinator Alert: {len(preview_targets)} at-risk students.

Details:
{preview_targets.to_string(index=False)}
"""
            # Preview coordinator email
            st.markdown("#### Preview Coordinator Email")
            st.code(body)

            # Send email
            success = send_automated_email(coord_email, "Course Risk Summary", body)
            if success:
                st.success("✅ Coordinator notified")
            else:
                st.error("❌ Failed to send coordinator email")
    else:
        st.info("No student data available for outreach.")
