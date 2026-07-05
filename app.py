from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import requests
import streamlit as st

APP_NAME = "SAP Outreach Daily Execution Dashboard"
PROJECT_START = date(2026, 7, 1)
TARGET_DATE = date(2026, 9, 23)
LOCAL_DB = Path("data") / "outreach.db"
SUPABASE_TABLE = "dashboard_records"

STATUS_OPTIONS = [
    "Not Contacted",
    "Sent",
    "Accepted",
    "Replied",
    "Meeting Booked",
    "Not Interested",
    "Follow-up Needed",
    "Pilot Discussion",
]
PRIORITY_OPTIONS = ["A", "B", "C"]
CHANNEL_OPTIONS = ["Email", "LinkedIn", "InMail", "Contact Form"]
YES_NO = ["No", "Yes"]
REPLY_TYPES = ["No Reply", "Positive", "Neutral", "Negative"]

TODAY_TASKS = [
    ("new_companies", "新增公司名单", 20),
    ("new_contact_entries", "新增可联系入口", 10),
    ("emails_sent", "发送邮件", 10),
    ("linkedin_notes", "LinkedIn 连接/备注", 10),
    ("inmails_sent", "InMail", 0),
    ("practice_minutes", "会议口述训练", 20),
    ("daily_report", "日报填写", 1),
]

PLAN_DEFAULTS = [
    ("Monday", "周一", "新增公司 80-100 家\n重点扩展名单池\n会议训练 20 分钟", 90, 0, 0, 0, 20),
    ("Tuesday", "周二", "补联系人入口 40 个\n找邮箱、LinkedIn、联系人\n会议训练 20 分钟", 0, 40, 0, 0, 20),
    ("Wednesday", "周三", "发送第一批触达 30-40 次\nLinkedIn + Email 双渠道\n会议训练 20 分钟", 0, 0, 20, 20, 20),
    ("Thursday", "周四", "发送第二批触达 30-40 次\n处理回复\n会议训练 20 分钟", 0, 0, 20, 20, 20),
    ("Friday", "周五", "数据复盘\n小幅调整邮件标题/首句\n不允许大改商业模式\n会议训练 20 分钟", 0, 0, 0, 0, 20),
    ("Saturday", "周六", "深度整理 CRM\n准备下周名单\n模拟会议 1 小时\n生活/运动安排", 0, 0, 0, 0, 60),
    ("Sunday", "周日", "轻复盘 1 小时\n安排下周任务\n休息", 0, 0, 0, 0, 0),
]

LEAD_COLUMNS = [
    "Company", "Country", "Website", "Priority", "SAP Services", "AMS Evidence",
    "Decision Maker", "Title", "LinkedIn URL", "Email", "Contact Form URL",
    "Channel Used", "Message Version", "Date Sent", "Follow-up Date", "Status",
    "Reply", "Meeting Booked", "Next Action", "Notes",
]


def secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return str(os.getenv(name, value or default)).strip()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class RecordStore:
    def __init__(self) -> None:
        self.url = secret("SUPABASE_URL")
        self.key = secret("SUPABASE_ANON_KEY")
        self.cloud_enabled = bool(self.url and self.key)
        if self.cloud_enabled:
            self.url = self.url.rstrip("/")
        else:
            self._init_local()

    def _init_local(self) -> None:
        LOCAL_DB.parent.mkdir(exist_ok=True)
        with sqlite3.connect(LOCAL_DB) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_records (
                    record_key TEXT PRIMARY KEY,
                    table_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def list(self, table_name: str) -> list[dict]:
        if self.cloud_enabled:
            endpoint = f"{self.url}/rest/v1/{SUPABASE_TABLE}?table_name=eq.{table_name}&select=record_key,payload,updated_at"
            response = requests.get(endpoint, headers=self.headers(), timeout=30)
            response.raise_for_status()
            return [dict(row["payload"], record_key=row["record_key"], updated_at=row.get("updated_at", "")) for row in response.json()]
        with sqlite3.connect(LOCAL_DB) as conn:
            rows = conn.execute(
                "SELECT record_key, payload, updated_at FROM dashboard_records WHERE table_name = ?",
                (table_name,),
            ).fetchall()
        return [dict(json.loads(payload), record_key=key, updated_at=updated) for key, payload, updated in rows]

    def upsert(self, table_name: str, record_key: str, payload: dict) -> None:
        payload = clean_payload(payload)
        if self.cloud_enabled:
            endpoint = f"{self.url}/rest/v1/{SUPABASE_TABLE}?on_conflict=record_key"
            body = [{"record_key": record_key, "table_name": table_name, "payload": payload}]
            response = requests.post(
                endpoint,
                headers=self.headers("resolution=merge-duplicates,return=minimal"),
                data=json.dumps(body, ensure_ascii=False),
                timeout=30,
            )
            response.raise_for_status()
            return
        with sqlite3.connect(LOCAL_DB) as conn:
            conn.execute(
                """
                INSERT INTO dashboard_records (record_key, table_name, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    table_name = excluded.table_name,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (record_key, table_name, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    def delete(self, record_key: str) -> None:
        if self.cloud_enabled:
            endpoint = f"{self.url}/rest/v1/{SUPABASE_TABLE}?record_key=eq.{record_key}"
            response = requests.delete(endpoint, headers=self.headers(), timeout=30)
            response.raise_for_status()
            return
        with sqlite3.connect(LOCAL_DB) as conn:
            conn.execute("DELETE FROM dashboard_records WHERE record_key = ?", (record_key,))


def clean_payload(payload: dict) -> dict:
    clean = {}
    for key, value in payload.items():
        if key in {"record_key", "updated_at"}:
            continue
        if pd.isna(value):
            clean[key] = ""
        elif isinstance(value, (int, float, str, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    clean["modified_at"] = now_iso()
    return clean


@st.cache_resource
def store() -> RecordStore:
    return RecordStore()


def rows_df(table_name: str) -> pd.DataFrame:
    return pd.DataFrame(store().list(table_name))


def ensure_defaults() -> None:
    s = store()
    existing_plan = {row.get("day_key") for row in s.list("schedule_plan")}
    for day_key, day_label, text, companies, contacts, emails, linkedin, practice in PLAN_DEFAULTS:
        if day_key not in existing_plan:
            s.upsert(
                "schedule_plan",
                f"schedule_plan:{day_key}",
                {
                    "day_key": day_key,
                    "day_label": day_label,
                    "plan_text": text,
                    "target_companies": companies,
                    "target_contacts": contacts,
                    "target_emails": emails,
                    "target_linkedin": linkedin,
                    "target_practice_minutes": practice,
                },
            )


def require_access() -> bool:
    passcode = secret("APP_PASSCODE")
    if not passcode:
        return True
    if st.session_state.get("access_granted"):
        return True
    st.title(APP_NAME)
    entered = st.text_input("Access passcode", type="password")
    if st.button("Enter"):
        if entered == passcode:
            st.session_state["access_granted"] = True
            st.rerun()
        else:
            st.error("Wrong passcode.")
    return False


def record_key(prefix: str) -> str:
    return f"{prefix}:{uuid4()}"


def page_setup() -> None:
    st.header("Cloud Deployment Setup")
    st.warning("This app is running without Supabase secrets, so it is using local fallback storage. For a real public multi-device app, deploy it with Supabase secrets.")
    st.code(
        """
create table if not exists dashboard_records (
  record_key text primary key,
  table_name text not null,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);
        """.strip(),
        language="sql",
    )
    st.write("Required deployment secrets: SUPABASE_URL, SUPABASE_ANON_KEY. Optional: APP_PASSCODE.")


def page_today() -> None:
    st.header("Today")
    selected_date = st.date_input("日期", value=date.today()).isoformat()
    existing = {row.get("task_key"): row for row in store().list("today_tasks") if row.get("task_date") == selected_date}
    rows = []
    for key, name, target in TODAY_TASKS:
        row = existing.get(key, {})
        rows.append(
            {
                "task_key": key,
                "task_name": name,
                "target": int(row.get("target", target) or 0),
                "actual": int(row.get("actual", 0) or 0),
                "completed": bool(row.get("completed", False)),
                "notes": row.get("notes", ""),
            }
        )
    df = pd.DataFrame(rows)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "task_key": st.column_config.TextColumn("Key", disabled=True),
            "task_name": st.column_config.TextColumn("任务", disabled=True),
            "target": st.column_config.NumberColumn("目标数量", min_value=0, step=1),
            "actual": st.column_config.NumberColumn("实际完成数量", min_value=0, step=1),
            "completed": st.column_config.CheckboxColumn("完成勾选"),
            "notes": st.column_config.TextColumn("备注"),
        },
    )
    if st.button("保存今日任务", type="primary"):
        for _, row in edited.iterrows():
            payload = row.to_dict()
            payload["task_date"] = selected_date
            store().upsert("today_tasks", f"today_tasks:{selected_date}:{payload['task_key']}", payload)
        st.success("已保存")
        st.rerun()

    completed_count = int(edited["completed"].sum())
    rate = round(completed_count / len(edited) * 100, 1) if len(edited) else 0
    c1, c2 = st.columns(2)
    c1.metric("今日完成率", f"{rate}%")
    c2.metric("今天是否达标", "是" if completed_count == len(edited) else "否")

    settings = {row.get("key"): row for row in store().list("settings")}
    tomorrow = st.text_area("明天第一件事", value=settings.get("tomorrow_first_thing", {}).get("value", ""), height=80)
    if st.button("保存明天第一件事"):
        store().upsert("settings", "settings:tomorrow_first_thing", {"key": "tomorrow_first_thing", "value": tomorrow})
        st.success("已保存")


def lead_payload_from_form(defaults: dict, key: str) -> dict:
    c1, c2, c3 = st.columns(3)
    company = c1.text_input("Company", defaults.get("Company", ""), key=f"{key}_company")
    country = c2.text_input("Country", defaults.get("Country", ""), key=f"{key}_country")
    priority = c3.selectbox("Priority", PRIORITY_OPTIONS, index=PRIORITY_OPTIONS.index(defaults.get("Priority", "B")) if defaults.get("Priority", "B") in PRIORITY_OPTIONS else 1, key=f"{key}_priority")
    website = st.text_input("Website", defaults.get("Website", ""), key=f"{key}_website")
    sap_services = st.text_area("SAP Services", defaults.get("SAP Services", ""), key=f"{key}_sap")
    ams_evidence = st.text_area("AMS Evidence", defaults.get("AMS Evidence", ""), key=f"{key}_ams")
    c1, c2 = st.columns(2)
    decision_maker = c1.text_input("Decision Maker", defaults.get("Decision Maker", ""), key=f"{key}_dm")
    title = c2.text_input("Title", defaults.get("Title", ""), key=f"{key}_title")
    linkedin_url = st.text_input("LinkedIn URL", defaults.get("LinkedIn URL", ""), key=f"{key}_li")
    email = st.text_input("Email", defaults.get("Email", ""), key=f"{key}_email")
    contact_form_url = st.text_input("Contact Form URL", defaults.get("Contact Form URL", ""), key=f"{key}_form")
    c1, c2, c3 = st.columns(3)
    channel_used = c1.text_input("Channel Used", defaults.get("Channel Used", ""), key=f"{key}_channel")
    message_version = c2.text_input("Message Version", defaults.get("Message Version", ""), key=f"{key}_version")
    status_default = defaults.get("Status", "Not Contacted")
    status = c3.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index(status_default) if status_default in STATUS_OPTIONS else 0, key=f"{key}_status")
    c1, c2 = st.columns(2)
    date_sent = c1.text_input("Date Sent", defaults.get("Date Sent", ""), placeholder="YYYY-MM-DD", key=f"{key}_sent")
    follow_up = c2.text_input("Follow-up Date", defaults.get("Follow-up Date", ""), placeholder="YYYY-MM-DD", key=f"{key}_follow")
    c1, c2 = st.columns(2)
    reply = c1.selectbox("Reply", YES_NO, index=YES_NO.index(defaults.get("Reply", "No")) if defaults.get("Reply", "No") in YES_NO else 0, key=f"{key}_reply")
    meeting = c2.selectbox("Meeting Booked", YES_NO, index=YES_NO.index(defaults.get("Meeting Booked", "No")) if defaults.get("Meeting Booked", "No") in YES_NO else 0, key=f"{key}_meeting")
    next_action = st.text_input("Next Action", defaults.get("Next Action", ""), key=f"{key}_next")
    notes = st.text_area("Notes", defaults.get("Notes", ""), key=f"{key}_notes")
    return {
        "Company": company, "Country": country, "Website": website, "Priority": priority,
        "SAP Services": sap_services, "AMS Evidence": ams_evidence, "Decision Maker": decision_maker,
        "Title": title, "LinkedIn URL": linkedin_url, "Email": email, "Contact Form URL": contact_form_url,
        "Channel Used": channel_used, "Message Version": message_version, "Date Sent": date_sent,
        "Follow-up Date": follow_up, "Status": status, "Reply": reply, "Meeting Booked": meeting,
        "Next Action": next_action, "Notes": notes,
    }


def page_leads() -> None:
    st.header("Leads")
    with st.expander("新增 Lead"):
        values = lead_payload_from_form({}, "new_lead")
        if st.button("新增 Lead", type="primary"):
            values["Created Date"] = date.today().isoformat()
            store().upsert("leads", record_key("leads"), values)
            st.success("Lead 已新增")
            st.rerun()

    with st.expander("导入 CSV"):
        uploaded = st.file_uploader("选择 CSV 文件", type=["csv"])
        if uploaded and st.button("导入 CSV"):
            df = pd.read_csv(uploaded).fillna("")
            count = 0
            for _, row in df.iterrows():
                payload = {col: str(row.get(col, "")) for col in LEAD_COLUMNS}
                payload["Priority"] = payload["Priority"] if payload["Priority"] in PRIORITY_OPTIONS else "B"
                payload["Status"] = payload["Status"] if payload["Status"] in STATUS_OPTIONS else "Not Contacted"
                payload["Created Date"] = date.today().isoformat()
                store().upsert("leads", record_key("leads"), payload)
                count += 1
            st.success(f"已导入 {count} 条 Lead")
            st.rerun()

    leads = rows_df("leads")
    search = st.text_input("搜索 Company / Decision Maker")
    c1, c2, c3 = st.columns(3)
    priority_filter = c1.multiselect("Priority", PRIORITY_OPTIONS)
    status_filter = c2.multiselect("Status", STATUS_OPTIONS)
    countries = sorted([c for c in leads.get("Country", pd.Series(dtype=str)).dropna().unique().tolist() if c]) if not leads.empty else []
    country_filter = c3.multiselect("Country", countries)

    filtered = leads.copy()
    if not filtered.empty:
        if search:
            filtered = filtered[filtered.get("Company", "").astype(str).str.contains(search, case=False, na=False) | filtered.get("Decision Maker", "").astype(str).str.contains(search, case=False, na=False)]
        if priority_filter:
            filtered = filtered[filtered["Priority"].isin(priority_filter)]
        if status_filter:
            filtered = filtered[filtered["Status"].isin(status_filter)]
        if country_filter:
            filtered = filtered[filtered["Country"].isin(country_filter)]

    export = filtered.drop(columns=["record_key", "updated_at", "modified_at"], errors="ignore")
    st.download_button("导出当前筛选 CSV", export.to_csv(index=False).encode("utf-8-sig"), f"leads_export_{date.today().isoformat()}.csv", "text/csv")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    st.subheader("编辑 / 删除 Lead")
    if leads.empty:
        st.info("还没有 Lead。")
        return
    options = [f"{row['record_key']} | {row.get('Company', '(No Company)')}" for _, row in leads.iterrows()]
    selected_label = st.selectbox("选择 Lead", options)
    selected_key = selected_label.split(" | ")[0]
    selected = leads[leads["record_key"] == selected_key].iloc[0].to_dict()
    values = lead_payload_from_form(selected, f"edit_{selected_key}")
    c1, c2 = st.columns(2)
    if c1.button("保存修改", type="primary"):
        store().upsert("leads", selected_key, values)
        st.success("已保存")
        st.rerun()
    if c2.button("删除 Lead"):
        store().delete(selected_key)
        st.warning("已删除")
        st.rerun()


def page_outreach_log() -> None:
    st.header("Outreach Log")
    with st.expander("新增触达记录", expanded=True):
        c1, c2 = st.columns(2)
        log_date = c1.date_input("Date", value=date.today())
        company = c2.text_input("Company")
        c1, c2, c3 = st.columns(3)
        decision_maker = c1.text_input("Decision Maker")
        channel = c2.selectbox("Channel", CHANNEL_OPTIONS)
        version = c3.text_input("Message Version", value="v1")
        c1, c2, c3 = st.columns(3)
        sent = c1.selectbox("Sent", YES_NO, index=1)
        reply = c2.selectbox("Reply", YES_NO)
        reply_type = c3.selectbox("Reply Type", REPLY_TYPES)
        meeting = st.selectbox("Meeting Booked", YES_NO)
        notes = st.text_area("Notes")
        if st.button("新增记录", type="primary"):
            store().upsert("outreach_log", record_key("outreach_log"), {
                "Date": log_date.isoformat(), "Company": company, "Decision Maker": decision_maker,
                "Channel": channel, "Message Version": version, "Sent": sent, "Reply": reply,
                "Reply Type": reply_type, "Meeting Booked": meeting, "Notes": notes,
            })
            st.success("已新增记录")
            st.rerun()

    logs = rows_df("outreach_log")
    st.dataframe(logs, use_container_width=True, hide_index=True)
    if logs.empty:
        return
    st.subheader("每日发送数量")
    daily = logs[logs["Sent"] == "Yes"].groupby("Date").size().reset_index(name="sent_count").sort_values("Date", ascending=False)
    st.dataframe(daily, use_container_width=True, hide_index=True)
    st.subheader("不同渠道回复率")
    st.dataframe(rate_table(logs, "Channel"), use_container_width=True, hide_index=True)
    st.subheader("不同 Message Version 回复率")
    st.dataframe(rate_table(logs, "Message Version"), use_container_width=True, hide_index=True)


def rate_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    grouped = df.groupby(group_col, dropna=False).agg(
        sent_count=("Sent", lambda s: int((s == "Yes").sum())),
        reply_count=("Reply", lambda s: int((s == "Yes").sum())),
        meeting_count=("Meeting Booked", lambda s: int((s == "Yes").sum())),
    )
    grouped["reply_rate"] = grouped.apply(lambda r: f"{round(r['reply_count'] / r['sent_count'] * 100, 1)}%" if r["sent_count"] else "0%", axis=1)
    return grouped.reset_index()


def page_practice() -> None:
    st.header("Practice")
    st.markdown("""
固定训练结构：
- Who I am
- Why I reached out
- Why China SAP consultants / why Europe
- How white-label collaboration works
- How to ask about their delivery capacity
- How to propose a low-risk next step
""")
    with st.expander("新增训练记录", expanded=True):
        c1, c2, c3 = st.columns(3)
        practice_date = c1.date_input("Date", value=date.today())
        minutes = c2.number_input("Minutes Practiced", min_value=0, step=5, value=20)
        confidence = c3.slider("Confidence score", 1, 10, 5)
        c1, c2, c3 = st.columns(3)
        opening = c1.selectbox("Opening practiced", YES_NO, index=1)
        objection = c2.selectbox("Objection practiced", YES_NO)
        closing = c3.selectbox("Closing practiced", YES_NO)
        notes = st.text_area("Notes")
        if st.button("新增训练记录", type="primary"):
            store().upsert("practice", record_key("practice"), {
                "Date": practice_date.isoformat(), "Minutes Practiced": int(minutes),
                "Opening practiced": opening, "Objection practiced": objection,
                "Closing practiced": closing, "Confidence score": int(confidence), "Notes": notes,
            })
            st.success("已新增训练记录")
            st.rerun()

    practices = rows_df("practice")
    if not practices.empty:
        pdates = pd.to_datetime(practices["Date"], errors="coerce").dt.date
        last_7 = date.today() - timedelta(days=6)
        recent = practices[pdates >= last_7]
        today_done = bool((practices["Date"] == date.today().isoformat()).any())
        avg_confidence = round(float(pd.to_numeric(practices["Confidence score"], errors="coerce").mean()), 1)
    else:
        recent = practices
        today_done = False
        avg_confidence = 0
    c1, c2, c3 = st.columns(3)
    c1.metric("最近 7 天训练次数", len(recent))
    c2.metric("平均 confidence score", avg_confidence)
    c3.metric("今天是否完成训练", "是" if today_done else "否")
    st.dataframe(practices, use_container_width=True, hide_index=True)


def safe_rate(num: int, den: int) -> str:
    return f"{round(num / den * 100, 1)}%" if den else "0%"


def page_dashboard() -> None:
    st.header("Dashboard")
    leads = rows_df("leads")
    logs = rows_df("outreach_log")
    practices = rows_df("practice")
    total = len(leads)
    a_count = int((leads.get("Priority", pd.Series(dtype=str)) == "A").sum()) if not leads.empty else 0
    contacted_status = ["Sent", "Accepted", "Replied", "Meeting Booked", "Follow-up Needed", "Pilot Discussion"]
    contacted = int(leads.get("Status", pd.Series(dtype=str)).isin(contacted_status).sum()) if not leads.empty else 0
    email_sent = int(((logs.get("Channel", pd.Series(dtype=str)) == "Email") & (logs.get("Sent", pd.Series(dtype=str)) == "Yes")).sum()) if not logs.empty else 0
    linkedin_sent = int(((logs.get("Channel", pd.Series(dtype=str)) == "LinkedIn") & (logs.get("Sent", pd.Series(dtype=str)) == "Yes")).sum()) if not logs.empty else 0
    inmail_sent = int(((logs.get("Channel", pd.Series(dtype=str)) == "InMail") & (logs.get("Sent", pd.Series(dtype=str)) == "Yes")).sum()) if not logs.empty else 0
    replies = int((logs.get("Reply", pd.Series(dtype=str)) == "Yes").sum()) if not logs.empty else 0
    positive = int((logs.get("Reply Type", pd.Series(dtype=str)) == "Positive").sum()) if not logs.empty else 0
    meetings = int((logs.get("Meeting Booked", pd.Series(dtype=str)) == "Yes").sum()) if not logs.empty else 0
    pilots = int((leads.get("Status", pd.Series(dtype=str)) == "Pilot Discussion").sum()) if not leads.empty else 0
    metrics = [
        ("总公司数量", total), ("A类公司数量", a_count), ("已触达数量", contacted),
        ("邮件发送数量", email_sent), ("LinkedIn发送数量", linkedin_sent), ("InMail发送数量", inmail_sent),
        ("回复数量", replies), ("正向回复数量", positive), ("会议数量", meetings), ("试点讨论数量", pilots),
        ("Reply Rate", safe_rate(replies, contacted)), ("Meeting Rate", safe_rate(meetings, contacted)),
        ("Positive Reply Rate", safe_rate(positive, contacted)),
    ]
    for start in range(0, len(metrics), 4):
        cols = st.columns(4)
        for col, (label, value) in zip(cols, metrics[start:start + 4]):
            col.metric(label, value)

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_new = 0
    if not leads.empty and "Created Date" in leads:
        week_new = int((pd.to_datetime(leads["Created Date"], errors="coerce").dt.date >= week_start).sum())
    if not logs.empty:
        log_dates = pd.to_datetime(logs["Date"], errors="coerce").dt.date
        week_logs = logs[log_dates >= week_start]
        week_contacted = int((week_logs["Sent"] == "Yes").sum())
        week_replies = int((week_logs["Reply"] == "Yes").sum())
        week_meetings = int((week_logs["Meeting Booked"] == "Yes").sum())
    else:
        week_contacted = week_replies = week_meetings = 0
    week_practice = 0
    if not practices.empty:
        week_practice = int((pd.to_datetime(practices["Date"], errors="coerce").dt.date >= week_start).sum())
    st.subheader("本周数据")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("本周新增公司", week_new)
    c2.metric("本周触达", week_contacted)
    c3.metric("本周回复", week_replies)
    c4.metric("本周会议", week_meetings)
    c5.metric("本周训练次数", week_practice)
    st.caption(f"项目开始：{PROJECT_START.isoformat()}；目标日期：{TARGET_DATE.isoformat()}")


def page_schedule() -> None:
    st.header("Schedule / Plan")
    plan = rows_df("schedule_plan")
    if plan.empty:
        ensure_defaults()
        plan = rows_df("schedule_plan")
    order = {day: i for i, (day, *_rest) in enumerate(PLAN_DEFAULTS)}
    plan["sort_order"] = plan["day_key"].map(order)
    plan = plan.sort_values("sort_order").drop(columns=["sort_order", "record_key", "updated_at", "modified_at"], errors="ignore")
    edited = st.data_editor(plan, hide_index=True, use_container_width=True)
    if st.button("保存周计划", type="primary"):
        for _, row in edited.iterrows():
            payload = row.to_dict()
            store().upsert("schedule_plan", f"schedule_plan:{payload['day_key']}", payload)
        st.success("已保存周计划")
        st.rerun()


def page_deploy() -> None:
    st.header("Deploy")
    mode = "Cloud/Supabase" if store().cloud_enabled else "Local fallback"
    st.metric("Current data mode", mode)
    st.write("要让任何设备打开同一个网页：把这个项目部署到 Streamlit Cloud，并配置 Supabase secrets。")
    st.code("streamlit run app.py", language="bash")
    st.write("需要的 secrets：")
    st.code("""
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_ANON_KEY="your-anon-public-key"
APP_PASSCODE="optional-passcode"
""".strip(), language="toml")


def main() -> None:
    st.set_page_config(page_title=APP_NAME, layout="wide")
    if not require_access():
        return
    ensure_defaults()
    st.title(APP_NAME)
    st.caption("公网部署版：Streamlit Web App + Supabase 云数据库。")
    page = st.sidebar.radio(
        "页面",
        ["Today", "Leads", "Outreach Log", "Practice", "Dashboard", "Schedule / Plan", "Deploy / Setup"],
    )
    if not store().cloud_enabled:
        st.sidebar.warning("当前未配置 Supabase，正在使用本地 fallback。部署公网前请配置云数据库。")
    if page == "Today":
        page_today()
    elif page == "Leads":
        page_leads()
    elif page == "Outreach Log":
        page_outreach_log()
    elif page == "Practice":
        page_practice()
    elif page == "Dashboard":
        page_dashboard()
    elif page == "Schedule / Plan":
        page_schedule()
    elif page == "Deploy / Setup":
        if store().cloud_enabled:
            page_deploy()
        else:
            page_setup()
            page_deploy()


if __name__ == "__main__":
    main()
