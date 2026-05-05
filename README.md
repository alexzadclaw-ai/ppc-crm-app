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

## Data privacy

Do **not** commit real CRM data to this repo. Keep CSV data in a separate private/local repo. This repo ignores `data/`, `crm/`, and `*.csv` by default.

## Requirements

Python 3.10+ only. No external packages required.
