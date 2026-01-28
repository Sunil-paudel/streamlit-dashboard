import streamlit as st
import plotly.express as px

def render_risk_scatter(df, total_target):
    """
    Renders the Risk Scatter tab content.
    """
    st.markdown("### Risk Scatter: Click a dot to see student details")
    color_map = {'Critical':'red','Warning':'yellow','Safe':'green'}
    if not df.empty and 'Risk_Category' in df.columns:
        # Prepare data for plotting
        plot_df = df.copy()
        # 1. Normalize Performance to % (to handle courses with different total marks)
        target = max(total_target, 1)
        plot_df['Performance_Perc'] = (plot_df['Final_Mark'] / target * 100).round(2)
        
        # 2. Ensure every dot is visible by adding a minimum size constant
        plot_df['Plot_Size'] = plot_df['Dwell_Hours'] + 5 

        fig = px.scatter(
            plot_df,
            x='Engagement_Score',
            y='Performance_Perc',
            size='Plot_Size',
            color='Risk_Category',
            color_discrete_map=color_map,
            hover_name='Name',
            hover_data={
                'Performance_Perc': False,
                'Score': True,
                'Assignments_Gap': True,
                'Quizzes_Gap': True,
                'Risk_Score': True,
                'Engagement_Score': ':.2f',
                'Plot_Size': False 
            },
            labels={
                'Engagement_Score':'Engagement (%)',
                'Performance_Perc':'Performance (%)',
                'Score': 'Current Score',
                'Assignments_Gap': 'Missed Assignments',
                'Quizzes_Gap': 'Missed Quizzes',
                'Risk_Score': 'Risk Score'
            },
            height=600
        )
        # Ensure axis ranges are -5 to 105 so nothing is cut off at the edges
        fig.update_yaxes(range=[-5, 105], title_text="Performance % (Weighted Mark / Total)")
        fig.update_xaxes(range=[-5, 105], title_text="Engagement Score (%)")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for scatter plot.")
