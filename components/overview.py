import streamlit as st
import pandas as pd

def render_overview(df, total_target, log_window_days, total_dwell_hours):
    """
    Renders the Overview tab content.
    """
    st.markdown("### Early Prevention Alerts")
    if not df.empty and 'Risk_Category' in df.columns:
        early_warn_df = df[df['Risk_Category'].isin(['Critical','Warning'])][['Name', 'Score', 'Assignments_Gap','Quizzes_Gap','Risk_Category']]

        if not early_warn_df.empty:
            st.dataframe(early_warn_df, width=1000) # Use numeric width instead of "stretch" specifically if older streamlit, but "stretch" is valid in newer. Original used "stretch".
        else:
            st.success("All students are on track.")

    else:
        st.info("No data available.")

    m1, m2, m3, m4 = st.columns(4)
    if not df.empty:
        m1.metric("Avg Final Mark", f"{df['Final_Mark'].mean():.2f} / {total_target:.2f}")
        if 'Status' in df.columns:
            m2.metric("Inactive Students", len(df[df['Status']=="Inactive"]))
        else:
             m2.metric("Inactive Students", 0)
        m3.metric(f"Total Dwell Hours ({log_window_days}d)", f"{total_dwell_hours:.2f}h")
        if 'Risk_Score' in df.columns:
            m4.metric("Avg Risk Score", f"{df['Risk_Score'].mean():.2f}%")
        else:
             m4.metric("Avg Risk Score", "0.00%")
