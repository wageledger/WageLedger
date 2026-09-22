# Wage Ledger — Payroll & Payslip Generator

A self-hosted payroll/payslip tool built around **many main companies**, each
holding its own client companies, with per-company employee master, monthly
attendance, payslip generation, and Excel reports.

## Recent changes — day-by-day attendance + PF/ESI
- **Monthly Attendance** is now a day-by-day grid (1 to the last day of the
  month) using the codes **P** (present), **W** (weekly off), **PP**
  (present + overtime), **OP** (weekly off + overtime), **L** (leave), **H**
  (half day present / half day leave) — matching the CAMS-style attendance
  sheet. **Download template** builds a copy for the month picked above,
  pre-filled with **P on weekdays and W on Saturdays/Sundays** for every
  employee on file, with live Excel formulas for **Days Worked, Weekly Off,
  Overtime, LEAVE, TOTAL** that recalculate as the day columns are edited.
  Uploading that same file back in reads the day columns (older
  Days Paid/LOP-style files are still accepted for backward compatibility).
- **Salary type rule:** *Per Day* employees are paid rate × (days worked +
  weekly off) from the attendance grid; *Per Month* employees are paid the
  fixed Basic Salary regardless of days/LOP, with only overtime (overtime
  days × per-day rate) added on top.
- **PF and ESI** are now calculated automatically as a percentage of Wages
  and shown as separate deduction lines on the payslip, payslip Excel/PDF
  exports and the Reports sheet. Both rates default to **12%** and are
  editable on the **Settings** tab (Super Admin) if you want ESI at its
  usual lower statutory rate instead.

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
| PUT    | `/api/companies/<id>` | Rename/move a client company (Super Admin, or its Admin) |
| DELETE | `/api/companies/<id>` | Delete a client company and its data (Super Admin; name must be echoed in `confirmName`) |
| GET    | `/api/settings` | Branding, theme and dashboard cards (any login) |
| PUT    | `/api/settings` | Change them (Super Admin) |
| GET    | `/api/data-files` | Every uploaded file with metadata (Super Admin) |

## Picking a main company
The **Main company** dropdown in the top bar (shown only when a login is
attached to more than one main company) now defaults to **All main companies**,
so everything in scope is on screen from the start. Pick a single main company
whenever you want to narrow the screen down to that main and its clients.

The **Working on** switcher is a **searchable picker** rather than a plain
dropdown, built for accounts carrying hundreds of client companies: click it,
type any part of a company name, address or main company, and pick from the
filtered list (grouped into main companies and client companies, capped at 200
rows at a time with a prompt to keep typing). It opens on **All companies**, a
combined view across every client company currently in scope. In that mode the Dashboard aggregates headcount, net pay,
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

## Companies tab — built for 300–400 clients
The flat grid of company cards is gone. Client companies are now grouped under
their main company in **collapsible sections**, each showing a live client
count, so the page opens as a short list of mains rather than hundreds of
cards. Inside a section the clients sit in a compact scrolling list that pages
25 at a time behind a **Show 25 more** button, and the company currently being
worked on is marked inline.

Above the list there is a **search box** covering company name, address and
main company name. Searching spans every main company at once and automatically
opens any section holding a match, with a running "x of y match" count.
**Expand all** / **Collapse all** are there for quick sweeps. With a single
main company in scope its section opens by default; with several they start
collapsed.

## Settings (Super Admin only)
A **Settings** tab, visible to the Super Admin alone, controls how the app
looks for *everyone*:
- **Branding** — the app name shown in the top bar, and a **logo** (PNG/JPG/SVG
  under 500 KB) that appears in the top bar and on every payslip header.
- **Theme colour** — eight accent presets (Blue, Indigo, Violet, Teal, Emerald,
  Amber, Rose, Charcoal). Picking one repaints buttons, highlights and the
  dashboard charts immediately as a preview; **Save settings** makes it stick
  for every login.
- **Dashboard cards** — a checkbox per card (Total Employees, Net Pay, Total
  Days Paid, LOP Days, payroll trend, attendance overview, headcount by
  department, pay dates calendar). Anything unticked is not rendered at all,
  and the remaining panels reflow two to a row.

Settings live on the server (`GET /api/settings` for any login,
`PUT /api/settings` for the Super Admin), so other logins pick up branding
changes the next time they load the app.

## Editing and deleting client companies
Each client company row in the Companies tab carries its own actions:
- **Edit** turns the row into an inline form — name, address, and (with more
  than one main company in scope) a picker to move the client to a different
  main. Open to the Super Admin and to any Admin assigned to that client's main
  company.
- **Delete** is the **Super Admin's alone**. It removes the company *and every
  employee record and month of attendance stored against it*, so it asks for
  the company name to be typed back before it runs, and reports how many stored
  records went with it. Main companies cannot be deleted here.

## Tabs
```
Dashboard | Companies | Employee Master | Monthly Attendance |
Payslip & Reports | Users & Access | Settings | Uploaded Data
```
- **Uploads are no longer a separate tab.** Each upload now lives in the tab it
  belongs to:
  - **Employee Master** — an **↑ Upload employees** button sits beside
    **+ Add employee** and opens the bulk-upload panel in place, with the
    template download, the recognised-column list and the per-row result report.
  - **Monthly Attendance** — an **↑ Upload attendance** button beside the month
    picker opens the attendance upload panel, with its pre-filled template (one
    row per employee on file). Uploaded rows fill the table below for checking,
    then **Save attendance for this month** commits them.
  Both panels name the company being uploaded into, and both need one specific
  company selected in **Working on**.
- **Payslip & Reports** — one tab holding both, with a selector between
  **Generate Payslip** and **Reports**. The employee dropdown has an
  **All employees** entry that produces a payslip for every employee in one
  run; they stack on screen and print one to a page. Without a single company
  selected the tab opens on the Reports half. **Generate payslip**,
  **Print payslip** and the download controls now sit in one row — see below.
- **Employee Master** holds the add/edit form behind an **+ Add employee**
  button (it opens by itself when you press Edit on a row) and the bulk upload
  behind the button next to it.

## Payslip output
The payslip row holds four controls together:

**Generate payslip** · **Print payslip** · *format* + *one file / separate* · **Download**

Print and Download stay disabled until payslips have been generated, and both
act on exactly the run on screen. Changing company or month clears them, so a
stale payslip can never be printed by mistake.

| Format | All in one file | Separate file per employee |
|---|---|---|
| **PDF** | One PDF, one payslip per page, looking exactly as it does on screen | One PDF per employee |
| **Excel** | One workbook: a **Summary** sheet (days, gross, deductions, net, pay date, with a total row) plus a full payslip sheet per employee | One workbook per employee |

PDF output uses jsPDF and html2canvas, loaded from cdnjs alongside the existing
xlsx and Chart.js libraries. In *separate* mode the browser may ask permission
to save several files at once.

## Employee Master form
- **Client Name** is prefilled with the company selected in **Working on** —
  which is what it is nearly always set to — and stays editable.
- **Bank Name** is a dropdown of the banks used most often here (SBI, HDFC,
  ICICI, Axis, Bank of Baroda, PNB, Canara, Union, Indian Bank, IOB, Kotak,
  IndusInd, Federal, City Union, TMB and more), with an **Other (type it in)**
  option for anything not listed. A bank already saved on a record — including
  one that arrived through a spreadsheet upload — is added to the list and
  preselected, so editing an employee never silently changes their bank.
  Bulk uploads still accept free text in the Bank Name column.

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

## Reports — one picker, one button
Single-company and combined reports are no longer separate cards. There is one
**Report for** dropdown carrying every scope in a single list, plus a month and
a **Download report** button:

| Group | Choice | What you get |
|---|---|---|
| Everything | All companies (main + client, or all your clients) | Every company in scope |
| Everything | Main companies only *(Super Admin)* | Just the main company records |
| By main company | *MainName* — main + N clients | **That main company together with all of its clients** |
| Main company data only | *MainName* (main company only) *(Super Admin)* | That main's own data on its own |
| Single client company | any one client | Just that company |

The dropdown follows the top-bar **Main company** filter, and companies a login
has no Reports permission for never appear.

- **One company selected** → a single-sheet workbook, exactly as before.
- **More than one** → a **Summary** sheet (company, main company, headcount,
  gross, deductions, net pay, with a total row) followed by one full sheet per
  company.

Below the picker, a live preview shows the totals for the chosen scope and
month, and for multi-company scopes a per-company breakdown table of exactly
what the file will contain.
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
