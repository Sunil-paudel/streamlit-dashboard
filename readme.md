# Student Risk Analytics Dashboard

A Streamlit-based analytics dashboard designed to identify students at risk of dropping out by analysing Moodle engagement, assessments, and performance data. The system enables early intervention through risk scoring, categorisation, and direct email outreach.

### Features


Moodle API integration (courses, quizzes, assignments, grades)

Student engagement analysis using Moodle logs

Configurable course weight system (quizzes & assignments)

Risk scoring and categorisation (Critical / Warning / Safe)

Detailed student performance views

Email outreach using Gmail SMTP

Interactive Streamlit dashboard

### Prerequisites


Before running the application, ensure you have:

Moodle Admin Access

Required to enable Moodle Web Services

Used to generate a Moodle API token

Moodle Logs

Download Moodle activity logs

Used to compute engagement metrics

Moodle Course ID

Required to fetch quizzes, assignments, and grades

Course Weight Configuration

Define which quizzes and assignments contribute to final scores

Only selected assessments affect calculations and dashboards

### SSL / TLS Setup for Localhost (macOS)


When running locally without HTTPS, Python may fail SSL verification.

Run the following command once to install trusted certificates:

/Applications/Python\ 3.13/Install\ Certificates.command

This enables TLS/SSL support for secure HTTPS requests on localhost.

### Installation & Environment Setup


1. Create and activate a virtual environment

python3 -m venv venv
source venv/bin/activate

2. Install dependencies


python3 -m pip install -r requirements.txt

Requirements


Ensure your requirements.txt includes Google services dependencies for Gmail SMTP and future Google integrations:

.env file
COORD_EMAIL=emailid
SMTP_USER=emailid
SMTP_PASS=app password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
MOODLE_URL=moodle url
MOODLE_TOKEN= moodle token


# Core
streamlit
pandas
numpy
plotly
python-dotenv
requests

# Moodle & Data
python-dateutil
pytz

# Email / SMTP
google-auth
google-auth-oauthlib
google-auth-httplib2

# SSL / Networking
certifi

### Running the Application


Start the Streamlit app with:

```bash
streamlit run apilog2.py
```

This will launch the dashboard on `http://localhost:8501`.

### Risk Calculation Methodology


The **Risk Score (0-100)** is a composite metric that identifies students who are disengaging or underperforming. It is calculated using a weighted sum of three components:

1. **Engagement Score (30%)**: 
   - Derived from Moodle Activity Logs.
   - Normalized 0-100 scale: `(50% Clicks + 50% Dwell Time)`.
   - `100` means the student is among the most active; `0` means no activity.

2. **Assessment Completion (40%)**: 
   - Based on the number of missed quizzes and assignments.
   - `100` means all assessments are submitted; score drops as items are missed.

3. **Performance Component (30%)**: 
   - The student's current weighted average grade (Final Mark).

**Formula:**
`Risk Score = 100 - (0.3 * Engagement + 0.4 * Completion + 0.3 * Performance)`

*A "perfect" student (high engagement, all submitted, 100% grade) gets a **Risk Score of 0.00**. A student with no activity and no submissions gets a **Risk Score of 100**.*

### Risk Categorisation


Students are automatically sorted into categories:

- **Critical**: Risk Score > 75 OR 3+ missed quizzes OR 2+ missed assignments.
- **Warning**: Risk Score 50-75 OR 1+ missed quiz OR 1+ missed assignment.
- **Safe**: Risk Score < 50.


### Student Detail View


Individual student performance breakdown

Scores converted into points based on course weight configuration

Visibility into missing submissions and quiz attempts

### Email Outreach (Gmail SMTP)


The dashboard allows coordinators to contact at-risk students directly.

Gmail SMTP Requirements

Google account with 2-Step Verification enabled

Generate an App Password for SMTP access

Use the App Password (not your Gmail password)

Environment Variables Example

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password
COORD_EMAIL=your_email@gmail.com

### Filtering & Outreach Tools


Filter students by:

Risk score

Risk category

Select multiple students

Send targeted intervention emails directly from the dashboard

### Tech Stack


Python 3.13

Streamlit

Pandas / NumPy

Moodle Web Services API

Gmail SMTP

Plotly

### Notes


Designed for local development and academic analytics use

Ensure Moodle data privacy and institutional policies are followed

### Future Enhancements


OAuth-based email integration

Real-time Moodle sync

Machine-learning-based risk prediction

Role-based access control

---
**Author:** Sunil Paudel  
**Project Type:** Academic Analytics / Early Intervention System

