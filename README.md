# SAP Outreach Daily Execution Dashboard

A Streamlit web app for the European SAP white-label delivery business development plan.

This version is cloud-deployable: any device can open the same web URL, and all data is stored in Supabase.

## Architecture

- Frontend/web app: Streamlit
- Cloud database: Supabase REST API
- Local fallback: SQLite, only when Supabase secrets are missing
- No LinkedIn login
- No automatic email sending
- Optional app passcode

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Without Supabase secrets, local runs use `data/outreach.db` as fallback storage.

## Supabase Setup

Create a Supabase project, open SQL Editor, and run:

```sql
create table if not exists dashboard_records (
  record_key text primary key,
  table_name text not null,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);
```

For an MVP, keep Row Level Security disabled on this table, or add policies that allow the anon key to read/write this table. Do not expose sensitive data publicly without an app passcode.

## Streamlit Cloud Deployment

1. Put this project in a GitHub repository.
2. Create a new Streamlit Cloud app from that repository.
3. Set the main file path to `app.py`.
4. Add these secrets in Streamlit Cloud:

```toml
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_ANON_KEY="your-anon-public-key"
APP_PASSCODE="your-private-passcode"
```

5. Deploy.
6. Open the Streamlit Cloud URL from any device.

## Files

```text
.
├── app.py
├── requirements.txt
├── README.md
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example
├── sample_data/
│   └── sample_leads.csv
└── data/
    └── outreach.db        # local fallback only
```
