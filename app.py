# app.py  ––  UNIQUE-COUNTER  +  RISK-DASHBOARD  (merged)
import os, smtplib, ssl, streamlit as st, pandas as pd
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="Logs analyser", layout="wide")
st.title("📊  logs_for_interns.csv  –  unique values & risk dashboard")

# ------------------------------------------------------------------
# 0.  Helper: load CSV (uploaded or default file)
# ------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_data(source) -> pd.DataFrame:
    """Return raw dataframe."""
    if source is None:
        try:
            return pd.read_csv("logs_for_interns.csv")
        except FileNotFoundError:
            st.error("No file uploaded and logs_for_interns.csv not found.")
            st.stop()
    return pd.read_csv(source)

# ------------------------------------------------------------------
# 1.  UNIQUE-VALUE COUNTER  (top of page)
# ------------------------------------------------------------------
uploaded = st.file_uploader("Upload CSV (leave empty to use logs_for_interns.csv)", type=["csv"])
df_raw = load_data(uploaded)

# basic cleaning
df_raw.columns = df_raw.columns.str.strip()
df_for_uniq = df_raw.drop(columns=["Time"], errors="ignore")

st.header("1️⃣  Unique-value counts (Time column ignored)")
uniq_counts = df_for_uniq.nunique().to_frame("Unique values").rename_axis("Column").reset_index()
st.dataframe(uniq_counts, use_container_width=True)

# detailed uniques for Event-related columns
event_cols = [c for c in df_for_uniq.columns if "event" in c.lower()]
if not event_cols:  # fallback
    event_cols = ["Event context", "Event name", "Description"]

with st.expander("Show unique values for Event columns"):
    for col in event_cols:
        if col not in df_for_uniq.columns:
            continue
        st.subheader(f"“{col}”")
        uniques = df_for_uniq[col].dropna().unique()
        st.dataframe(pd.Series(uniques, name=col), use_container_width=True)

st.divider()

# ------------------------------------------------------------------
# 2.  RISK DASHBOARD  (bottom of page)
# ------------------------------------------------------------------
st.header("2️⃣  🚨  Early-risk notifier")

# ---------- 2-a  risk-engine --------------------------------------
def compute_risk(df: pd.DataFrame) -> pd.DataFrame:
    """Return DF + risk_score column (0-7)."""
    df = df.copy()
    course_id, assign_cmid = 83, 2889
    deadline = pd.Timestamp("2025-12-17 23:59:59")
    now = pd.Timestamp.now()
    df["Time"] = pd.to_datetime(df["Time"], dayfirst=True, errors="coerce")

    # 1. zero-resource
    opened = df[df["Event name"].eq("Course module viewed") &
                df["Description"].str.contains(str(assign_cmid), na=False)]
    has_opened = opened["User full name"].unique()
    df["zero_resource"] = ~df["User full name"].isin(has_opened)

    # 2. last-minute first access
    first_access = df.groupby("User full name")["Time"].min()
    df["lastmin_first"] = df["User full name"].map(
        lambda x: (deadline - first_access.get(x, deadline)).total_seconds() / 3600 <= 24
    )

    # 3. grade-book snooping
    recent = df[df["Time"].ge(now - pd.Timedelta(hours=48))]
    snoop = recent[recent["Event name"].eq("Grade user report viewed")]
    snoop_cnt = snoop.groupby("User full name").size()
    df["grade_snoop"] = df["User full name"].map(snoop_cnt).fillna(0)

    # 4. no draft + status checks
    status = df[df["Event name"].eq("The status of the submission has been viewed.")]
    status_cnt = status.groupby("User full name").size()
    df["status_checks"] = df["User full name"].map(status_cnt).fillna(0)
    df["no_draft"] = True  # replace with real check if you have mdl_assign_submission

    # 5. section-hopping binge
    df["minute5"] = df["Time"].dt.floor("5min")
    hop = df[df["Event name"].eq("Course viewed")].groupby(["User full name", "minute5"]).size()
    binge = hop.groupby("User full name").max()
    df["hop_binge"] = df["User full name"].map(binge).fillna(0)

    # 6. help silence
    help_events = ["Forum post created", "Message sent", "FAQ viewed"]
    helped = df[df["Event name"].isin(help_events)]["User full name"].unique()
    df["help_count"] = df["User full name"].isin(helped)

    # 7. geo jumps
    df["ip_block"] = df["IP address"].str.extract(r"(\d+\.\d+\.\d+)\.\d+", expand=False)
    geo = df[df["IP address"].ne("")].groupby("User full name")["ip_block"].nunique()
    df["geo_jumps"] = df["User full name"].map(geo).fillna(0)

    # assemble score
    df["risk_score"] = (
        df["zero_resource"].astype(int) +
        df["lastmin_first"].astype(int) +
        (df["grade_snoop"] >= 3).astype(int) +
        (df["no_draft"] & (df["status_checks"] >= 4)).astype(int) +
        (df["hop_binge"] >= 5).astype(int) +
        (~df["help_count"]).astype(int) +
        (df["geo_jumps"] >= 2).astype(int)
    )
    return df

df_risk = compute_risk(df_raw)

# ---------- 2-b  display table ------------------------------------
risk_cut = st.slider("Show students with risk ≥", 0, 7, 4)
risky = (df_risk[["User full name", "risk_score"]]
         .drop_duplicates()
         .sort_values("risk_score", ascending=False)
         .query("risk_score >= @risk_cut"))
st.dataframe(risky, use_container_width=True)

if risky.empty:
    st.success("No students above selected risk threshold 🎉")
    st.stop()

# ---------- 2-c  e-mail notifier ----------------------------------
st.subheader("Send e-mail alert")
coord_mail = st.text_input("Coordinator e-mail", value=os.getenv("COORD_EMAIL", ""))
smtp_user = os.getenv("SMTP_USER")
smtp_pass = os.getenv("SMTP_PASS")
smtp_srv = os.getenv("SMTP_SERVER", "smtp.gmail.com")
smtp_port = int(os.getenv("SMTP_PORT", "465"))

if st.button("📮  Notify coordinator now", type="primary"):
    if not coord_mail:
        st.error("Please enter a coordinator e-mail"); st.stop()

    body_lines = [
        f"Early-alert summary – {pd.Timestamp.now():%Y-%m-%d %H:%M}",
        f"Students with risk-score ≥ {risk_cut}: {len(risky)}", ""
    ] + [f"{row['User full name']}  (score {row['risk_score']})" for _, row in risky.iterrows()]
    body = "\n".join(body_lines)

    msg = EmailMessage()
    msg["Subject"] = f"HVEC702 early-risk alert – {len(risky)} students"
    msg["From"] = smtp_user
    msg["To"] = coord_mail
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_srv, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        st.success(f"✅  Mail sent to {coord_mail}")
        if "notif_log" not in st.session_state:
            st.session_state.notif_log = []
        st.session_state.notif_log.append(
            f"{pd.Timestamp.now():%H:%M}  –  mail sent ({len(risky)} students)"
        )
    except Exception as e:
        st.error("Send failed"); st.exception(e)

# ---------- 2-d  in-app log ---------------------------------------
if st.session_state.get("notif_log"):
    with st.expander("Notification log"):
        st.text("\n".join(st.session_state.notif_log))