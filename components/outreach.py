import streamlit as st
import pandas as pd
import requests
import json
import os
from dotenv import load_dotenv

from utils import send_automated_email  # keep your existing email util

# ======================================================
# LOAD ENV
# ======================================================
load_dotenv()

MOODLE_URL = os.getenv("MOODLE_URL")
MOODLE_TOKEN = os.getenv("MOODLE_TOKEN")


# ======================================================
# MOODLE MESSAGE SENDER (INLINE)
# ======================================================
def send_moodle_message(touserid: int, message: str, clientmsgid: str = ""):
    """
    Sends a Moodle private message using core_message_send_instant_messages

    Returns:
        (success: bool, response: dict|str, payload: dict)
    """

    endpoint = f"{MOODLE_URL}/webservice/rest/server.php"

    payload = {
        "wstoken": MOODLE_TOKEN,
        "wsfunction": "core_message_send_instant_messages",
        "moodlewsrestformat": "json",
        "messages[0][touserid]": touserid,
        "messages[0][text]": message,
        "messages[0][textformat]": 1,
        "messages[0][clientmsgid]": clientmsgid,
    }

    try:
        r = requests.post(endpoint, data=payload, timeout=15)
        try:
            data = r.json()
        except Exception:
            return False, r.text, payload

        # Moodle errors come back as dict with "exception"
        if isinstance(data, dict) and "exception" in data:
            return False, data, payload

        return True, data, payload

    except Exception as e:
        return False, str(e), payload


# ======================================================
# STREAMLIT OUTREACH UI
# ======================================================
def render_outreach(df, weight_config, coord_email, group_mapping=None):

    st.markdown("### Student Outreach & Messaging")

    if df.empty or 'Risk_Score' not in df.columns:
        st.info("No student data available.")
        return

    # ================= FILTERS =================
    st.markdown("#### Segmentation Filters")

    col1, col2 = st.columns(2)

    with col1:
        t_val = st.slider("Risk Score Threshold", 0, 100, 50)
        cat_filter = st.multiselect(
            "Risk Category",
            ['Critical', 'Warning', 'Safe'],
            default=['Critical', 'Warning']
        )

    with col2:
        item_names = [cfg['name'] for cfg in weight_config.values()]
        selected_items = st.multiselect("Assessments", item_names)
        score_threshold = st.slider("Score Threshold (%)", 0, 100, 40, 10)

    narrow_by_risk = st.checkbox("Require BOTH Risk + Activity (AND)", value=False)

    # ================= APPLY FILTERS =================
    risk_mask = (df['Risk_Score'] >= t_val) | (df['Risk_Category'].isin(cat_filter))
    item_mask = pd.Series(False, index=df.index)

    if selected_items:
        for idx, row in df.iterrows():
            for key, cfg in weight_config.items():
                if cfg['name'] in selected_items:
                    raw = row.get(f"raw_{key}", 0)
                    max_pts = row.get(f"max_{key}", cfg['weight']) or cfg['weight']
                    pct = (raw / max_pts * 100) if max_pts else 0
                    if pct < score_threshold:
                        item_mask[idx] = True
                        break

    if selected_items and narrow_by_risk:
        final_mask = risk_mask & item_mask
        filter_desc = "Risk AND Activity"
    elif selected_items:
        final_mask = risk_mask | item_mask
        filter_desc = "Risk OR Activity"
    else:
        final_mask = risk_mask
        filter_desc = "Risk only"

    # Pre-process group and class names for fast lookup
    group_id_to_name = {str(g['id']): g['name'] for g in group_mapping.get('groups', [])} if group_mapping else {}
    group_to_grouping = {}
    if group_mapping and 'groupings' in group_mapping:
        for gping in group_mapping['groupings']:
            gp_name = gping.get('name', 'N/A')
            for grp in gping.get('groups', []):
                group_to_grouping[str(grp['id'])] = gp_name

    # Add Class/Group columns to the dataframe
    if group_mapping:
        def get_cls(uid):
            u_grps = group_mapping['user_to_groups'].get(str(uid), [])
            gp_names = list(set([group_to_grouping.get(str(gid), "No Class") for gid in u_grps]))
            return ", ".join(gp_names) if gp_names else "No Class"
        
        def get_grp(uid):
            u_grps = group_mapping['user_to_groups'].get(str(uid), [])
            g_names = [group_id_to_name.get(str(gid), "Unknown") for gid in u_grps]
            return ", ".join(g_names) if g_names else "No Group"

        df['Class'] = df['User_ID'].apply(get_cls)
        df['Group'] = df['User_ID'].apply(get_grp)
    else:
        df['Class'] = "N/A"
        df['Group'] = "N/A"

    # ================= TARGET LIST =================
    cols = [
        "Name", "Class", "Group", "Email", "User_ID",
        "Risk_Score", "Risk_Category",
        "Assignments_Gap", "Quizzes_Gap",
        "Days_Since_Last"
    ]

    # Add raw grade columns for all assessments
    # Pre-calculate counts to handle duplicate names
    name_counts = {}
    for k, cfg in weight_config.items():
        name = cfg['name']
        name_counts[name] = name_counts.get(name, 0) + 1

    unique_headers = {}
    for key, cfg in weight_config.items():
        grademax = cfg.get('grademax', 100.0)
        if name_counts.get(cfg['name'], 0) > 1:
            col_name = f"{cfg['name']} [ID:{cfg['id']}] ({grademax})"
        else:
            col_name = f"{cfg['name']} ({grademax})"
            
        unique_headers[key] = col_name
        cols.append(col_name)
        # Create display columns from raw data
        df[col_name] = df[f"raw_{key}"].fillna(0).round(2)

    targets = df[final_mask][cols].copy()

    st.markdown(f"### Targets ({filter_desc})")

    if targets.empty:
        st.info("No students match the criteria.")
        return

    targets.insert(0, "Select", True)

    edited = st.data_editor(
        targets,
        column_config={
            "Select": st.column_config.CheckboxColumn("Contact?", default=True),
            "Risk_Score": st.column_config.NumberColumn("Risk (%)", format="%.2f"),
            **{unique_headers[key]: st.column_config.NumberColumn(f"{cfg['name']}") for key, cfg in weight_config.items()}
        },
        disabled=cols,
        hide_index=True
    )

    final_targets = edited[edited["Select"]]

    # ================= TEMPLATE =================
    st.markdown("---")
    st.subheader("Message Template")

    template = st.text_area(
        "Used for BOTH Email and Moodle",
        value="""<div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto; border: 1px solid #eee; border-radius: 10px; padding: 20px; border-top: 5px solid #2e7d32;">
    <h3 style="color: #2e7d32; margin-top: 0;">Course Progress Check-in</h3>
    <p>Hi <strong>{Name}</strong>,</p>
    <p>We're reaching out to provide a quick update on your course engagement. Here is a summary of your current progress:</p>
    
    <div style="background-color: #f1f8e9; padding: 15px; border-radius: 8px; margin: 20px 0;">
        <ul style="list-style: none; padding: 0; margin: 0;">
            <li style="margin-bottom: 8px;"><strong>Risk Category:</strong> {Risk_Category}</li>
            <li style="margin-bottom: 8px;"><strong>Pending Assignments:</strong> {Assignments_Gap}</li>
            <li style="margin-bottom: 8px;"><strong>Pending Quizzes:</strong> {Quizzes_Gap}</li>
            <li style="margin-bottom: 0;"><strong>Last Active:</strong> {Days_Since_Last} days ago</li>
        </ul>
    </div>
    
    <p>If you're facing any academic or technical challenges, please reach out to your coordinator. We're here to help you succeed!</p>
    
    <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
    <p style="font-size: 0.9em; color: #666; margin-bottom: 0;">Kind regards,<br><strong>Student Support Team</strong></p>
</div>""",
        height=320
    )

    # ================= CHANNELS =================
    st.markdown("---")
    st.subheader("Delivery Channels")

    send_email = st.checkbox("Send Email", value=True)
    send_moodle = st.checkbox("Send Moodle Message", value=True)

    # ================= SEND =================
    if st.button(f"Contact Students ({len(final_targets)})"):
        email_ok = 0
        moodle_ok = 0

        for _, r in final_targets.iterrows():
            body = template.format(
                Name=r["Name"],
                Risk_Category=r["Risk_Category"],
                Assignments_Gap=r["Assignments_Gap"],
                Quizzes_Gap=r["Quizzes_Gap"],
                Days_Since_Last=int(r["Days_Since_Last"])
            )

            st.markdown(f"### Student Record: {r['Name']}")
            
            with st.expander(f"Message Preview"):
                st.markdown(body, unsafe_allow_html=True)

            # -------- EMAIL --------
            if send_email:
                st.write("Sending email...")
                if send_automated_email(
                    r["Email"],
                    "A quick check-in about your course progress",
                    body,
                    is_html=True
                ):
                    email_ok += 1
                    st.success(f"Email sent to {r['Email']}")
                else:
                    st.error(f"Email failed: {r['Name']}")

            # -------- MOODLE --------
            if send_moodle:
                st.write("Sending Moodle message...")
                
                ok, res, payload = send_moodle_message(
                    touserid=int(r["User_ID"]),
                    message=body,
                    clientmsgid=f"risk-{r['User_ID']}"
                )

                if not ok:
                    st.error(f"Moodle message failed: {r['Name']}")
                    if isinstance(res, dict) and "message" in res:
                        st.error(f"Error details: {res['message']}")
                else:
                    moodle_ok += 1

            st.markdown("---")

        # Final summary
        st.markdown("## Delivery Summary")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Emails Sent", f"{email_ok}/{len(final_targets)}")
        with col2:
            st.metric("Moodle Messages Sent", f"{moodle_ok}/{len(final_targets)}")

    st.markdown("---")
    st.subheader("Coordinator Summary Report")

    if st.button("Send to Coordinator"):
        # Generate HTML Table
        table_rows = ""
        for _, row in targets.iterrows():
            # Color code risk
            risk_color = "#ffebee" if row['Risk_Category'] == 'Critical' else "#fff3e0" if row['Risk_Category'] == 'Warning' else "#f1f8e9"
            
            table_rows += f"""
            <tr style="background-color: {risk_color}; border-bottom: 1px solid #ddd;">
                <td style="padding: 8px;"><strong>{row['Name']}</strong></td>
                <td style="padding: 8px;">{row['Risk_Category']}</td>
                <td style="padding: 8px;">{row['Risk_Score']}%</td>
                <td style="padding: 8px;">{row['Assignments_Gap']}</td>
                <td style="padding: 8px;">{row['Quizzes_Gap']}</td>
                <td style="padding: 8px;">{int(row['Days_Since_Last'])} days</td>
            </tr>
            """

        html_body = f"""
        <div style="font-family: Arial, sans-serif; color: #333; max-width: 800px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
            <div style="background-color: #2e7d32; padding: 20px; text-align: center;">
                <h2 style="color: white; margin: 0;">🎓 Course Risk Report</h2>
            </div>
            
            <div style="padding: 20px; background-color: #fafafa;">
                <p style="font-size: 16px;">Hello Coordinator,</p>
                <p>Here is the current risk analysis report. We found <strong>{len(targets)} students</strong> requiring attention.</p>
                
                <table style="width: 100%; border-collapse: collapse; margin-top: 15px; background-color: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <thead>
                        <tr style="background-color: #424242; color: white;">
                            <th style="padding: 10px; text-align: left;">Name</th>
                            <th style="padding: 10px; text-align: left;">Risk Level</th>
                            <th style="padding: 10px; text-align: left;">Score</th>
                            <th style="padding: 10px; text-align: left;">Missed Asg</th>
                            <th style="padding: 10px; text-align: left;">Missed Quiz</th>
                            <th style="padding: 10px; text-align: left;">Last Active</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>

                <p style="margin-top: 20px; font-size: 14px; color: #666;">
                    Please review their profiles in the dashboard for more details.
                </p>
            </div>
            <div style="background-color: #eee; padding: 10px; text-align: center; font-size: 12px; color: #777;">
                Generated by Moodle Analytics Dashboard
            </div>
        </div>
        """
        
        # Show Preview
        with st.expander("Preview Coordinator Email", expanded=True):
            st.markdown(html_body, unsafe_allow_html=True)

        if send_automated_email(coord_email, f"Course Risk Report ({len(targets)} Students)", html_body, is_html=True):
            st.success(f"Report sent to {coord_email}")
        else:
            st.error("Failed to notify coordinator")