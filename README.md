# WIOM Recon — Books vs GST Workflow Platform

Multi-user reconciliation platform. The 9-agent engine processes the monthly
Zoho Books vs GST recon Excel; results are stored month-wise, auto-split
state-wise, and routed to the accounts team for remarks and to the manager
for approval — with a full audit trail of who changed what.

## Run locally (this PC)

Double-click **`RUN WIOM RECON.bat`**, then open **http://localhost:5000**

First login (manager / admin):
- **Email:** `saurav.mishra@wiom.in`
- **Password:** `admin123`  ← change after first login (Team → edit yourself)

## How it works

1. **New Recon** (admin) → pick the month, upload the Zoho Books recon `.xlsx`.
   The 9 agents run, the Excel report is generated, and every invoice row is
   stored in the database, auto-tagged with its **state** (from the GSTIN).
2. **Team** (admin) → create accounts-team logins. Assign each person their
   state(s); they will only see those rows. Leave blank = all-India.
3. **Detail & Remarks** → the team filters to their state/month, writes a
   **reason + remark** on each mismatch. Status moves `open → remarked`.
4. The **manager** reviews and **Approves** or **Resolves** each row
   (`→ approved / resolved`). Every action records the user's name + IST time.
5. **Dashboard** → cumulative **State × Month** view of total differences and
   approval progress. Each monthly run's Excel is downloadable any time.

## Roles
- **admin** (manager): upload, see all states, approve/resolve, manage users.
- **accounts** (team): write remarks/reasons on assigned states only.

## Vendor master
GSTIN → vendor name is synced into the `vendor_master` table from the Books
export + GST cache on every run. To switch to the **live Zoho Books API**
later, populate `VendorMaster` from the API (source=`zoho_api`) — nothing else
changes. Hook lives in `persist.py: sync_vendor_master()`.

## Moving to cloud later (no code changes)
The app is local-first but cloud-portable. On the cloud host, set env vars:

```
SECRET_KEY=<long random string>
DATABASE_URL=postgresql://user:pass@host:5432/wiom   # switch SQLite -> Postgres
ADMIN_PASSWORD=<strong password>
PORT=8080
```

Then run with a production server:
```
pip install -r requirements.txt
gunicorn "app:app" --bind 0.0.0.0:$PORT --workers 2
```
Everything (login, monthly data, remarks, audit) carries over unchanged — only
the storage backend swaps from a local SQLite file to a managed Postgres DB.

## Files
| File | Purpose |
|------|---------|
| `app.py` | Flask app, engine orchestration, workflow APIs |
| `models.py` | DB schema (users, runs, rows, vendor master, audit) |
| `auth.py` | Login + user management |
| `persist.py` | Stores engine output into DB; vendor-master sync |
| `config.py` | Env-driven config (local ↔ cloud) |
| `state_codes.py` | GSTIN → state mapping |
| `agents/` | The 9-agent reconciliation engine (unchanged) |
| `templates/` | UI (login, dashboard, detail, upload, team) |
