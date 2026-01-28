import streamlit as st

def render_methodology(formula_config):
    """
    Renders the Methodology tab content.
    """
    # Extract config values
    act_w = formula_config.get('activity_weight', 0.5)
    comp_w = formula_config.get('completion_weight', 0.5)
    eng_ow = formula_config.get('engagement_overall_weight', 0.6)
    perf_ow = formula_config.get('performance_overall_weight', 0.4)

    st.markdown("### Methodology")
    st.write(f"""
    - **Unified Engagement Score ({int(eng_ow*100)}%)**: A composite score of activity and progress:
        - **Activity ({int(act_w*100)}% of engagement)**: Combined Clicks and Dwell Time (page activity).
        - **Assessment Completion ({int(comp_w*100)}% of engagement)**: Percentage of **overdue** items submitted.
    - **Performance Component ({int(perf_ow*100)}%)**: Quality of marks (percentage of available points achieved).
    - **Risk Score** = 100 - ({eng_ow} * Unified Engagement + {round(perf_ow, 2)} * Performance)
    - **Risk Categories**:
        - Critical: Risk Score > 75 OR 3+ missed overdue quizzes OR 2+ missed overdue assignments.
        - Warning: Risk Score 50-75 OR 2+ missed overdue quizzes OR 1+ missed overdue assignment.
        - Safe: Risk Score < 50.
    """)
