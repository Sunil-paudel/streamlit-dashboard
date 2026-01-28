# ========================================================================================
# File: utils.py
# Description: General Utility Functions.
#
# Purpose:
#   - Contains helper functions that are reusable across the application.
#   - Handles generic tasks such as email sending (SMTP) and specific calculation
#     logic (Dwell Time) that doesn't strictly belong in the core business logic or API layer.
#
# Key Functions:
#   - calculate_dwell_time: Computes time spent by analyzing timestamp diffs in logs.
#   - send_automated_email: Handles the low-level details of sending emails via SMTP_SSL.
#
# Dependencies:
#   - smtplib, ssl, email.mime (for emails)
#   - config (for email credentials)
# ========================================================================================

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import streamlit as st
import pandas as pd
from config import SMTP_USER, SMTP_PASS, SMTP_SERVER, SMTP_PORT

def calculate_dwell_time(group, time_col):
    """
    Calculates dwell time in hours for a group of log entries.
    """
    group = group.sort_values(by=time_col)
    durations = group[time_col].diff().dt.total_seconds() / 60
    view_mask = group['Event name'].str.contains('viewed', case=False, na=False)
    return round(durations[view_mask].clip(upper=30).sum() / 60, 2)

def send_automated_email(to_email, subject, body, is_html=False):
    """
    Sends an automated email using SMTP.
    """
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg.attach(MIMEText(body, "html" if is_html else "plain"))
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ssl.create_default_context()) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        st.warning(f"Email error: {e}")
        return False
