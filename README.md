# Page Profit Check CRM App

A small CSV-backed CRM web app for Page Profit Check outreach.

The app code can live in GitHub. The real CRM data CSV should stay in a separate private/local repo and be passed at runtime.

## Run locally

```bash
python3 scripts/crm_webapp.py --host 127.0.0.1 --port 8787 --csv /path/to/private/page-profit-check-leads-from-prototypes.csv
```

Or set an environment variable:

```bash
export PPC_CRM_CSV=/path/to/private/page-profit-check-leads-from-prototypes.csv
python3 scripts/crm_webapp.py
```

Open:

```text
http://127.0.0.1:8787/
```

Status changes save directly to the configured CSV.


## CRM workflow features

- Colorful status pills and status dropdowns
- Needs-action-today card and filter, driven by `Next Follow-up Date`
- Lead detail drawer with editable priority, status, follow-up date, owner, value, issue, angle, and notes
- Activity log / notes timeline saved into the CSV
- Backend endpoints for status updates, lead edits, and notes
- Automatic CSV schema upgrade for workflow columns such as `Activity Log`

## Data privacy

Do **not** commit real CRM data to this repo. Keep CSV data in a separate private/local repo. This repo ignores `data/`, `crm/`, and `*.csv` by default.

## Requirements

Python 3.10+ only. No external packages required.
