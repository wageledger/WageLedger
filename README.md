# Wage Ledger — Payroll & Payslip Generator

A self-hosted payroll/payslip tool built around **many main companies**, each
holding its own client companies, with per-company employee master, monthly
attendance, payslip generation, and Excel reports.

## Structure

```
Main company A                Main company B
├── Client company 1          ├── Client company 4
├── Client company 2          └── Client company 5
└── Client company 3
```

A main company has its own employees and attendance too, but that data belongs
to the **Super Admin alone**. Admin logins are assigned to one or more main
companies and only ever see the **client companies** underneath them — never
the main company itself.

## Stack
- **Frontend:** single static HTML/CSS/JS page (`static/index.html`) — same
  payroll logic as before, with a login screen, a company switcher in the top
  bar, and role-aware navigation.
- **Backend:** Python + Flask (`app.py`) — sessions, password hashing, and the
  scope/permission model on top of SQLite storage.
- **Database:** SQLite file (`wage_ledger.db`), created automatically on first
  run.

## Login tiers

| Role | What it can do |
|---|---|
| **Super Admin** | Creates and renames **main companies**, adds client companies under any of them, and is the only login that can open a main company's own employee and attendance data. Downloads every company's data in one workbook (main companies included) **or** the main companies on their own. Creates Admin and User logins and decides which main companies each one is assigned to. |
| **Admin** | Sees only the **client companies** under the main companies it has been assigned to. The main company record — its name, address and its own employees — is invisible to an Admin. Creates client companies under those mains, uses every module on them, and downloads one combined Excel workbook covering all its client companies. Creates User logins, which inherit its assignment automatically. |
| **User** | No Companies tab at all — it picks a company from the switcher in the top bar. Works on exactly the same client companies as the Admin that created it, through whichever of the four modules are switched on, and can download the combined report for those client companies. Cannot create or edit companies. |

Module switches (Employee Master / Attendance / Payslip / Reports) are set per
User login from the **Users & Access** tab and apply across every company in
that login's scope. Admin and Super Admin logins always hold all four. Every
grant is enforced server-side on each read and write, not just hidden in the UI.

## Uploaded Data (Super Admin)
A **Uploaded Data** tab, visible to the Super Admin only, lists every
Employee Master and Attendance file that any Admin or User has ever uploaded,
across every main and client company — company name, which main it belongs
to, module type, month (for attendance), record count, and when it was last
updated. Each row has a **Delete** button: if an Admin or User uploads the
wrong file, delete it here and they'll need to upload it again. Backed by
`GET /api/data-files` and the existing `DELETE /api/storage/<key>` endpoint.

### Assigning a main company to an Admin
On the **Users & Access** tab, each login shows a row of main-company
checkboxes (Super Admin only). Tick the main companies that login should work
under and press **Save main companies**. When you move an Admin, its own User
logins move with it, so an Admin and its team never drift apart.

### First run
The first time you start the server a default Super Admin login is created and
printed to the console:

```
Username: superadmin
Password: <randomly generated>
```

Log in with it, use **Change password** (top right), then add your main
companies on the **Companies** tab and your Admin logins on **Users & Access**.

## How data is stored
- `users` holds logins plus their four module switches.
- `companies` holds main companies (`is_main=1`) and client companies, each
  with a `parent_id` pointing at its main company.
- `user_mains` records which main companies each Admin/User login is assigned
  to. Super Admins need no rows there — they reach everything.
- Employee Master and monthly Attendance records keep their original shape,
  saved as JSON blobs (`employees_<companyId>`, `monthly_<companyId>_<month>`)
  in the `kv_store` table, gated by the scope and module rules above.

### Upgrading from an earlier version
Nothing to do by hand. On first start this version adds the new columns and
tables, files every existing client company under the original `main` company,
folds any old per-company grants down into each User's module switches, and
assigns every existing Admin/User login to that original main company — so the
app looks exactly as it did, just with room for more main companies.

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/auth/login` | Sign in |
| POST   | `/api/auth/logout` | Sign out |
| GET    | `/api/auth/me` | Current login |
| POST   | `/api/auth/change-password` | Change your own password |
| GET    | `/api/companies` | Companies, mains and modules for this login |
| POST   | `/api/companies` | Create a client company under a main (Super Admin/Admin) |
| POST   | `/api/companies/main` | Create a main company (Super Admin) |
| PUT    | `/api/companies/main/<id>` | Rename a main company (Super Admin) |
| GET/POST | `/api/users` | List / create logins |
| PUT    | `/api/users/<id>` | Enable/disable, reset password |
| POST   | `/api/users/<id>/modules` | Set a login's module switches |
| POST   | `/api/users/<id>/mains` | Assign a login's main companies (Super Admin) |
| GET/PUT/DELETE | `/api/storage/<key>` | Employee/attendance blobs (scope-gated) |
| GET    | `/api/export` | Raw JSON dump of what this login can reach |
| GET    | `/api/data-files` | Every uploaded file with metadata (Super Admin) |

## Deploying (GitHub + Vercel)

This app now runs on either database automatically:
- **No `DATABASE_URL` set** -> SQLite, a local file - exactly as before, for
  `python app.py` on your own machine or any host with a writable disk.
- **`DATABASE_URL` set** -> Postgres, via `psycopg` - required on Vercel,
  since its filesystem is read-only and SQLite cannot write there.

### 1. Push this folder to GitHub
```
git init
git add .
git commit -m "Wage Ledger"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### 2. Create a hosted Postgres database
Easiest path: in the Vercel dashboard, **Storage -> Create Database ->
Postgres** (this provisions a Neon-backed database and can auto-attach its
`DATABASE_URL` to your project). A standalone neon.tech or supabase.com
project works the same way - copy its connection string either way.

### 3. Import the repo into Vercel
**Add New -> Project**, pick this GitHub repo. Vercel reads `vercel.json`
and routes every request through `api/index.py`, which just imports the
Flask `app` from `app.py` - no other build configuration is needed.

### 4. Set environment variables
Project Settings -> Environment Variables:
- `DATABASE_URL` - the Postgres connection string (auto-filled if you used
  Vercel's own Storage integration in step 2).
- `SECRET_KEY` - required whenever `DATABASE_URL` is set. Generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"` and paste the
  result. Without this, Flask sessions (logins) would reset on every cold
  start, since there's nowhere to persist a generated key on disk.
- Optionally `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD` to set the first
  Super Admin login's credentials instead of the `superadmin` /
  `superadmin@123` default.

### 5. Deploy
Click **Deploy**. On the very first request, `app.py` creates the Postgres
tables and the default Super Admin login automatically - nothing to run by
hand.

### 6. First login
Sign in with the Super Admin credentials from step 4 (or the default),
then **Change password** immediately from the top bar.

### Notes
- Re-deploying (new commits, or `vercel --prod`) reuses the same Postgres
  database - the data isn't reset.
- Local development is untouched: without `DATABASE_URL`, `python app.py`
  still uses `wage_ledger.db` on disk exactly as before.

## Picking a main company
The **Main company** dropdown in the top bar (shown only when a login is
attached to more than one main company) now defaults to **All main companies**,
so everything in scope is on screen from the start. Pick a single main company
whenever you want to narrow the screen down to that main and its clients.

The **Working on** switcher has no "choose a company" placeholder any more: it
opens on **All companies**, a combined view across every client company
currently in scope. In that mode the Dashboard aggregates headcount, net pay,
days paid, LOP, the six-month trend, department split and pay dates across all
of those companies, and the Employee Master shows every employee with extra
**Main Company** and **Client Company** columns. Attendance and Payslip still
need one specific company, so those two tabs stay closed until one is picked.

Switching companies keeps you on the tab you were already on, as long as that
company allows it.

## Adding a client company
The **Add a client company** card is always available to a Super Admin or
Admin. With more than one main company in scope it carries its own **Main
company** picker, so a client can be filed under any main without changing the
top bar. After saving, the app **stays on the Companies tab** and confirms the
new company inline — it no longer jumps to the Dashboard.

## Employee bulk upload — Client Name check
If the uploaded sheet has a **Client Name** column, a row is only accepted when
its Client Name matches the client company being uploaded into. Rows naming a
different client company (a CAMS row inside UNIFI, say) are rejected and listed
in the result, so one company's data can never land in another's. Matching
ignores case, punctuation and common suffixes (Pvt, Ltd, Company, Services and
so on). Sheets with no Client Name column at all upload exactly as before.

## Tabs
```
Dashboard | Companies | Employee Master | Monthly Attendance | Uploads |
Payslip & Reports | Users & Access | Uploaded Data
```
- **Uploads** — every spreadsheet upload on one screen. A selector at the top
  switches between **Upload Employee Master** and **Upload Monthly
  Attendance**, and only the chosen format's screen is shown. The attendance
  screen takes a month, a file and offers a pre-filled template (one row per
  employee on file); matched rows are saved into that month straight away and
  can then be reviewed on the Monthly Attendance tab. Uploads always go into
  the company selected in **Working on**, so the tab is closed in the
  "All companies" view.
- **Payslip & Reports** — one tab holding both, with a selector between
  **Generate Payslip** and **Reports**. The employee dropdown has an
  **All employees** entry that produces a payslip for every employee in one
  run; they stack on screen and print one to a page. Without a single company
  selected the tab opens on the Reports half. The Reports screen shows a
  single plain summary line (employee count, gross, deductions, net pay,
  month) before you download — no dashboard-style stat cards or charts.
- **Employee Master** now holds the employee data only: the add/edit form sits
  behind an **+ Add employee** button (it opens by itself when you press Edit
  on a row) and bulk upload has moved to the Uploads tab.

## Employees on file — search, filters and columns
The Employee Master table has a toolbar with:
- a **Search** box covering every field on the record (name, UAN, employee no,
  bank details, client name and the rest),
- **Company** (combined view only), **Department**, **Designation** and
  **Client Name** dropdowns, each built from the data actually on file,
- a **Columns** button opening a structured checklist — grouped exactly like
  the Employee Master form (Company, 1 · Basic Details, 2 · Classification,
  3 · Statutory & Bank Details, 4 · Reference Details), laid out as an aligned
  grid of bordered checkbox tiles (4 per row, fewer on narrower screens) so
  everything lines up instead of wrapping raggedly. Each group has its own
  **All** / **None** links and an "on / total" count. Only ticked columns are
  drawn, in the order shown, and the choice is remembered in the browser,
- **Clear**, which resets the search and all filters.

A line above the table shows how many employees and columns are currently
visible. In the combined view **Download employee data** exports exactly what
is on screen (filters and chosen columns included); for a single company it
still exports the full upload-template layout.

## Reports
- **Single company report** — pick any one company in your scope and a month.
- **Combined report** —
  - Super Admin: one workbook with a sheet for every main company *and* every
    client company, plus a separate **Download main companies only** button.
  - Admin / User: one workbook with a sheet for each of their client companies.
  - With a main company picked in the top bar, the combined report covers just
    that main company's companies, and the file is named after it.
- **Raw data export** — `/api/export` returns JSON limited to what the login
  can see. Add `?company=<id>` for a single company, `?scope=mains` for main
  companies only, or `?scope=clients` for client companies only.

## Run it locally

```bash
cd wage-ledger
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** and sign in with the Super Admin
credentials printed in the console.

The database file `wage_ledger.db` (and a `.flask_secret` session key file) are
created next to `app.py` the first time you run the app. To reset everything,
stop the server and delete both files.

## Notes
- Excel upload/download (employee bulk upload, attendance upload, payroll
  reports) happens client-side in the browser via the `xlsx` JS library.
- Sessions are cookie-based; the secret key is generated once into
  `.flask_secret`. Delete that file to force everyone to log in again.
- This is a small self-hosted app (SQLite, Flask dev server). If you host it
  beyond your own network, put it behind HTTPS and a production WSGI server
  (gunicorn/uwsgi).
