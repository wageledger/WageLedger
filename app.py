"""
Wage Ledger backend
--------------------
Flask + SQLite backend for the Wage Ledger app.

STRUCTURE
    There can be MANY main companies. Every client company belongs to exactly
    one main company (its parent). A main company also has its own employees
    and attendance, but that data belongs to the Super Admin alone.

LOGIN TIERS
    super_admin  - owns everything. Creates and edits main companies, creates
                   client companies under any main, and is the ONLY login that
                   can see or touch a main company's own employee/attendance
                   data. Downloads every company's data (main companies
                   included), or just the main companies on their own.
                   Creates Admin and User logins and decides which main
                   companies each one is assigned to.

    admin        - assigned to one or more main companies by the Super Admin,
                   and only ever sees the CLIENT companies under those mains.
                   The main company record itself - its name, address and its
                   own employee data - is invisible to an admin. Creates client
                   companies under the mains it is assigned to, works on every
                   module for those clients, and downloads a combined report of
                   all its client companies. Creates User logins, which
                   automatically inherit the admin's main-company assignment.

    user         - never sees the Companies tab and cannot create or edit
                   companies. Works on the same client companies as the admin
                   that created it (picking one from the company switcher in
                   the top bar), through whichever of the four modules are
                   switched on for that login, and can download the combined
                   report for those client companies.

DATA MODEL
    users        - login accounts (username/password hash/role) plus the four
                   module switches: can_employees / can_attendance /
                   can_payslip / can_reports.
    companies    - main companies (is_main=1, parent_id NULL) and client
                   companies (is_main=0, parent_id = the main company's id).
    user_mains   - which main companies a login is assigned to. Super admins
                   need no rows here; they reach everything.
    kv_store     - generic blob store holding employees_<companyId> and
                   monthly_<companyId>_<month> records (unchanged shape),
                   gated by the scope + module rules above.

Run:
    pip install -r requirements.txt
    python app.py

Then open http://localhost:5000 in your browser.
On first run a default Super Admin login is created - see the console
output for the generated username/password.
"""

import functools
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = "/tmp/wage_ledger.db"
STATIC_DIR = os.path.join(BASE_DIR, "static")

MODULES = ("employees", "attendance", "payslip", "reports")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

app.secret_key = os.environ["FLASK_SECRET_KEY"]

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('super_admin','admin','user')),
            created_by    INTEGER,
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            can_employees  INTEGER NOT NULL DEFAULT 1,
            can_attendance INTEGER NOT NULL DEFAULT 1,
            can_payslip    INTEGER NOT NULL DEFAULT 1,
            can_reports    INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL DEFAULT '',
            address    TEXT NOT NULL DEFAULT '',
            is_main    INTEGER NOT NULL DEFAULT 0,
            parent_id  TEXT,
            owner_id   INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_mains (
            user_id INTEGER NOT NULL,
            main_id TEXT NOT NULL,
            UNIQUE(user_id, main_id)
        )
        """
    )
    conn.commit()

    # --- upgrades from earlier versions ------------------------------------
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    modules_added = False
    for col in ("can_employees", "can_attendance", "can_payslip", "can_reports"):
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER NOT NULL DEFAULT 1")
            modules_added = True
    conn.commit()

    # Older versions kept per-(user, company) grants in an "access" table.
    has_access = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='access'"
    ).fetchone()
    if modules_added and has_access:
        for row in conn.execute(
            """
            SELECT user_id, MAX(can_employees) AS e, MAX(can_attendance) AS a,
                   MAX(can_payslip) AS p, MAX(can_reports) AS r
            FROM access GROUP BY user_id
            """
        ).fetchall():
            conn.execute(
                "UPDATE users SET can_employees=?, can_attendance=?, can_payslip=?, can_reports=? "
                "WHERE id=? AND role='user'",
                (row["e"], row["a"], row["p"], row["r"], row["user_id"]),
            )
        conn.commit()
    if has_access:
        conn.execute("DROP TABLE access")
        conn.commit()

    # Client companies now hang off a specific main company.
    co_cols = {r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()}
    if "parent_id" not in co_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN parent_id TEXT")
        # Everything that existed before belonged to the single 'main' company.
        conn.execute("UPDATE companies SET parent_id='main' WHERE is_main=0")
        conn.commit()

    # Ensure at least one main company exists.
    row = conn.execute("SELECT id FROM companies WHERE is_main=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO companies (id,name,address,is_main,parent_id,owner_id,created_at) "
            "VALUES ('main','Main Company','',1,NULL,NULL,?)",
            (now_iso(),),
        )
        conn.commit()

    # Ensure at least one super admin login exists.
    row = conn.execute("SELECT id FROM users WHERE role='super_admin'").fetchone()
    if row is None:
        # Default bootstrap credentials. Override via env vars before first
        # run if you want something else baked in from the start.
        default_user = os.environ.get("SUPERADMIN_USERNAME", "superadmin")
        default_pass = os.environ.get("SUPERADMIN_PASSWORD", "superadmin@123")
        conn.execute(
            "INSERT INTO users (username,password_hash,role,created_by,active,created_at) VALUES (?,?,?,?,1,?)",
            (default_user, generate_password_hash(default_pass), "super_admin", None, now_iso()),
        )
        conn.commit()
        print("=" * 64)
        print(" First run: created default Super Admin login")
        print(f"   Username: {default_user}")
        print(f"   Password: {default_pass}")
        print(" This is a fixed default password - change it immediately")
        print(" after your first login, especially before/after deploying.")
        print("=" * 64)

    # Any pre-existing admin/user login was working under the original single
    # main company - assign them to it so nothing disappears on upgrade.
    if conn.execute("SELECT COUNT(*) AS n FROM user_mains").fetchone()["n"] == 0:
        first_main = conn.execute(
            "SELECT id FROM companies WHERE is_main=1 ORDER BY created_at"
        ).fetchone()
        if first_main:
            for u in conn.execute("SELECT id FROM users WHERE role IN ('admin','user')").fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO user_mains (user_id, main_id) VALUES (?,?)",
                    (u["id"], first_main["id"]),
                )
            conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()


def login_required(roles=None):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify({"error": "not authenticated"}), 401
            if roles and user["role"] not in roles:
                return jsonify({"error": "forbidden"}), 403
            g.current_user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def get_company(company_id):
    db = get_db()
    return db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()


# ---------------------------------------------------------------------------
# Scope + permission helpers
# ---------------------------------------------------------------------------
def user_modules(user):
    """The four module switches. Super admins and admins hold all four."""
    if user["role"] in ("super_admin", "admin"):
        return {m: True for m in MODULES}
    return {
        "employees": bool(user["can_employees"]),
        "attendance": bool(user["can_attendance"]),
        "payslip": bool(user["can_payslip"]),
        "reports": bool(user["can_reports"]),
    }


def all_main_ids():
    db = get_db()
    return [r["id"] for r in db.execute("SELECT id FROM companies WHERE is_main=1").fetchall()]


def assigned_main_ids(user):
    """Main company ids this login is attached to. Super admins get them all."""
    if user["role"] == "super_admin":
        return all_main_ids()
    db = get_db()
    return [
        r["main_id"]
        for r in db.execute("SELECT main_id FROM user_mains WHERE user_id=?", (user["id"],)).fetchall()
    ]


def main_companies_for(user):
    """Main company rows this login is attached to."""
    ids = assigned_main_ids(user)
    if not ids:
        return []
    db = get_db()
    marks = ",".join("?" * len(ids))
    return db.execute(
        f"SELECT * FROM companies WHERE is_main=1 AND id IN ({marks}) ORDER BY created_at", ids
    ).fetchall()


def in_scope(user, company):
    """Can this login reach this company's payroll data at all?"""
    if company is None:
        return False
    if user["role"] == "super_admin":
        return True
    # A main company's own employee/attendance data is the Super Admin's alone.
    if company["is_main"]:
        return False
    return company["parent_id"] in assigned_main_ids(user)


def effective_perms(user, company):
    """Module booleans for this login on this company, plus manage/isOwner flags."""
    if not in_scope(user, company):
        d = {m: False for m in MODULES}
        d["manage"] = False
        d["isOwner"] = False
        return d
    d = user_modules(user)
    d["manage"] = user["role"] in ("super_admin", "admin")
    d["isOwner"] = user["role"] == "super_admin" or (
        user["role"] == "admin" and company["owner_id"] == user["id"]
    )
    return d


def can_read_employees(user, company):
    p = effective_perms(user, company)
    return p["employees"] or p["payslip"] or p["reports"]


def can_write_employees(user, company):
    return effective_perms(user, company)["employees"]


def can_read_attendance(user, company):
    p = effective_perms(user, company)
    return p["attendance"] or p["payslip"] or p["reports"]


def can_write_attendance(user, company):
    return effective_perms(user, company)["attendance"]


def workable_companies(user):
    """
    The companies this login actually works in.

    Super admin: every main company and every client company.
    Admin / user: only the client companies under the mains they're assigned
    to - the main companies themselves stay out of sight.
    """
    db = get_db()
    if user["role"] == "super_admin":
        return db.execute(
            "SELECT * FROM companies ORDER BY is_main DESC, created_at"
        ).fetchall()
    ids = assigned_main_ids(user)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.execute(
        f"SELECT * FROM companies WHERE is_main=0 AND parent_id IN ({marks}) ORDER BY created_at",
        ids,
    ).fetchall()


def _company_json(user, c, main_names):
    return {
        "id": c["id"],
        "name": c["name"],
        "address": c["address"],
        "isMain": bool(c["is_main"]),
        "parentId": c["parent_id"],
        "mainName": main_names.get(c["parent_id"], ""),
        "ownerId": c["owner_id"],
        "perms": effective_perms(user, c),
    }


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401
    if not row["active"]:
        return jsonify({"error": "This login has been disabled"}), 403
    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify({"id": row["id"], "username": row["username"], "role": row["role"]})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = current_user()
    if user is None:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"]})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required()
def auth_change_password():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    old = body.get("old_password") or ""
    new = body.get("new_password") or ""
    if not check_password_hash(user["password_hash"], old):
        return jsonify({"error": "Current password is incorrect"}), 400
    if len(new) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    db = get_db()
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Company endpoints
# ---------------------------------------------------------------------------
@app.route("/api/companies", methods=["GET"])
@login_required()
def companies_list():
    """Everything the front end needs to draw itself for this login."""
    user = g.current_user
    db = get_db()
    main_names = {
        r["id"]: r["name"] for r in db.execute("SELECT id, name FROM companies WHERE is_main=1").fetchall()
    }
    companies = [_company_json(user, c, main_names) for c in workable_companies(user)]

    # Main companies this login may file client companies under. Super admins
    # can also rename them; admins never see them as companies of their own.
    mains = [
        {"id": m["id"], "name": m["name"], "address": m["address"]}
        for m in main_companies_for(user)
    ]
    return jsonify({
        "role": user["role"],
        "modules": user_modules(user),
        "mains": mains,
        "canEditMains": user["role"] == "super_admin",
        "companies": companies,
    })


@app.route("/api/companies", methods=["POST"])
@login_required(roles=("super_admin", "admin"))
def companies_create():
    """Create a client company under one of the main companies in scope."""
    user = g.current_user
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    main_id = (body.get("mainId") or "").strip()
    if not name:
        return jsonify({"error": "Company name is required"}), 400

    allowed_mains = assigned_main_ids(user)
    if not allowed_mains:
        return jsonify({"error": "You are not assigned to any main company yet"}), 403
    if not main_id:
        if len(allowed_mains) == 1:
            main_id = allowed_mains[0]
        else:
            return jsonify({"error": "Choose which main company this client belongs to"}), 400
    if main_id not in allowed_mains:
        return jsonify({"error": "That main company is not yours to add clients to"}), 403

    cid = "co_" + secrets.token_hex(8)
    owner_id = user["id"] if user["role"] == "admin" else None
    db = get_db()
    db.execute(
        "INSERT INTO companies (id,name,address,is_main,parent_id,owner_id,created_at) VALUES (?,?,?,0,?,?,?)",
        (cid, name, address, main_id, owner_id, now_iso()),
    )
    db.commit()
    main_names = {
        r["id"]: r["name"] for r in db.execute("SELECT id, name FROM companies WHERE is_main=1").fetchall()
    }
    return jsonify(_company_json(user, get_company(cid), main_names))


@app.route("/api/companies/<company_id>", methods=["PUT"])
@login_required(roles=("super_admin", "admin"))
def company_update(company_id):
    """
    Rename a client company, change its address, or move it to a different
    main company. The Super Admin may edit any client; an Admin only the
    clients under the main companies it is assigned to.
    """
    user = g.current_user
    company = get_company(company_id)
    if company is None:
        return jsonify({"error": "Unknown company"}), 404
    if company["is_main"]:
        return jsonify({"error": "Use the main company form to edit a main company"}), 400

    allowed_mains = assigned_main_ids(user)
    if company["parent_id"] not in allowed_mains:
        return jsonify({"error": "That company is not yours to edit"}), 403

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    main_id = (body.get("mainId") or company["parent_id"]).strip()
    if not name:
        return jsonify({"error": "Company name is required"}), 400
    if main_id not in allowed_mains:
        return jsonify({"error": "That main company is not yours to move clients into"}), 403

    db = get_db()
    db.execute(
        "UPDATE companies SET name=?, address=?, parent_id=? WHERE id=?",
        (name, address, main_id, company_id),
    )
    db.commit()
    main_names = {
        r["id"]: r["name"] for r in db.execute("SELECT id, name FROM companies WHERE is_main=1").fetchall()
    }
    return jsonify(_company_json(user, get_company(company_id), main_names))


@app.route("/api/companies/<company_id>", methods=["DELETE"])
@login_required(roles=("super_admin",))
def company_delete(company_id):
    """
    Delete a client company and every employee/attendance record stored
    against it. Super Admin only, and the caller must echo the company's
    exact name back in "confirmName" so this cannot fire by accident.
    """
    company = get_company(company_id)
    if company is None:
        return jsonify({"error": "Unknown company"}), 404
    if company["is_main"]:
        return jsonify({"error": "Main companies cannot be deleted here"}), 400

    body = request.get_json(silent=True) or {}
    if (body.get("confirmName") or "").strip() != (company["name"] or "").strip():
        return jsonify({"error": "Type the company name exactly to confirm deletion"}), 400

    db = get_db()
    removed = db.execute(
        "DELETE FROM kv_store WHERE key = ? OR key LIKE ?",
        ("employees_" + company_id, "monthly_" + company_id + "_%"),
    ).rowcount
    db.execute("DELETE FROM companies WHERE id=?", (company_id,))
    db.commit()
    return jsonify({"id": company_id, "deleted": True, "dataRecordsRemoved": removed})


# ---------------------------------------------------------------------------
# App settings - logo, theme and dashboard cards.
# Read by every login (the whole app is branded by them); written by the
# Super Admin alone.
# ---------------------------------------------------------------------------
SETTINGS_KEY = "app_settings"
MAX_LOGO_CHARS = 700_000          # ~500 KB of base64, plenty for a logo

DEFAULT_SETTINGS = {
    "brandName": "Wage Ledger",
    "logo": "",
    "theme": "blue",
    "pfRate": 12,
    "esiRate": 12,
    "dashCards": {
        "employees": True, "netPay": True, "daysPaid": True, "lop": True,
        "trend": True, "attendance": True, "departments": True, "payDates": True,
    },
}


def _read_settings():
    row = get_db().execute("SELECT value FROM kv_store WHERE key=?", (SETTINGS_KEY,)).fetchone()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))      # deep copy
    if row:
        try:
            saved = json.loads(row["value"])
        except (ValueError, TypeError):
            saved = {}
        if isinstance(saved, dict):
            for k in ("brandName", "logo", "theme"):
                if isinstance(saved.get(k), str):
                    settings[k] = saved[k]
            for k in ("pfRate", "esiRate"):
                if isinstance(saved.get(k), (int, float)):
                    settings[k] = saved[k]
            cards = saved.get("dashCards")
            if isinstance(cards, dict):
                for k in settings["dashCards"]:
                    if k in cards:
                        settings["dashCards"][k] = bool(cards[k])
    return settings


@app.route("/api/settings", methods=["GET"])
@login_required()
def settings_get():
    return jsonify(_read_settings())


@app.route("/api/settings", methods=["PUT"])
@login_required(roles=("super_admin",))
def settings_put():
    body = request.get_json(silent=True) or {}
    settings = _read_settings()

    if "brandName" in body:
        settings["brandName"] = (str(body.get("brandName") or "").strip() or "Wage Ledger")[:60]
    if "theme" in body:
        settings["theme"] = (str(body.get("theme") or "blue").strip() or "blue")[:20]
    if "logo" in body:
        logo = str(body.get("logo") or "")
        if logo and not logo.startswith("data:image/"):
            return jsonify({"error": "Logo must be an image"}), 400
        if len(logo) > MAX_LOGO_CHARS:
            return jsonify({"error": "That image is too large - please use one under 500 KB"}), 400
        settings["logo"] = logo
    for k in ("pfRate", "esiRate"):
        if k in body:
            try:
                rate = float(body.get(k))
            except (TypeError, ValueError):
                return jsonify({"error": "PF and ESI rates must be numbers"}), 400
            if rate < 0 or rate > 100:
                return jsonify({"error": "PF and ESI rates must be between 0 and 100"}), 400
            settings[k] = rate
    cards = body.get("dashCards")
    if isinstance(cards, dict):
        for k in settings["dashCards"]:
            if k in cards:
                settings["dashCards"][k] = bool(cards[k])

    db = get_db()
    db.execute(
        "INSERT INTO kv_store (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (SETTINGS_KEY, json.dumps(settings), now_iso()),
    )
    db.commit()
    return jsonify(settings)


@app.route("/api/companies/main", methods=["POST"])
@login_required(roles=("super_admin",))
def main_company_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    if not name:
        return jsonify({"error": "Main company name is required"}), 400
    db = get_db()
    mid = "main_" + secrets.token_hex(6)
    db.execute(
        "INSERT INTO companies (id,name,address,is_main,parent_id,owner_id,created_at) "
        "VALUES (?,?,?,1,NULL,NULL,?)",
        (mid, name, address, now_iso()),
    )
    db.commit()
    return jsonify({"id": mid, "name": name, "address": address, "isMain": True})


@app.route("/api/companies/main/<company_id>", methods=["PUT"])
@login_required(roles=("super_admin",))
def main_company_update(company_id):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip()
    if not name:
        return jsonify({"error": "Main company name is required"}), 400
    company = get_company(company_id)
    if company is None or not company["is_main"]:
        return jsonify({"error": "Unknown main company"}), 404
    db = get_db()
    db.execute("UPDATE companies SET name=?, address=? WHERE id=?", (name, address, company_id))
    db.commit()
    return jsonify({"id": company_id, "name": name, "address": address, "isMain": True})


# ---------------------------------------------------------------------------
# User management endpoints
# ---------------------------------------------------------------------------
def _user_json(row, db):
    mains = db.execute(
        "SELECT c.id, c.name FROM user_mains um JOIN companies c ON c.id = um.main_id "
        "WHERE um.user_id=? ORDER BY c.created_at",
        (row["id"],),
    ).fetchall()
    owned = db.execute("SELECT id, name FROM companies WHERE owner_id=?", (row["id"],)).fetchall()
    return {
        "id": row["id"], "username": row["username"], "role": row["role"],
        "active": bool(row["active"]), "createdBy": row["created_by"],
        "modules": user_modules(row),
        "mains": [{"id": m["id"], "name": m["name"]} for m in mains],
        "ownedCompanies": [{"id": o["id"], "name": o["name"]} for o in owned],
    }


@app.route("/api/users", methods=["GET"])
@login_required(roles=("super_admin", "admin"))
def users_list():
    user = g.current_user
    db = get_db()
    if user["role"] == "super_admin":
        rows = db.execute("SELECT * FROM users WHERE id!=? ORDER BY created_at", (user["id"],)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM users WHERE created_by=? AND role='user' ORDER BY created_at", (user["id"],)
        ).fetchall()
    return jsonify([_user_json(r, db) for r in rows])


@app.route("/api/users", methods=["POST"])
@login_required(roles=("super_admin", "admin"))
def users_create():
    user = g.current_user
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "user"
    if not username or len(password) < 6:
        return jsonify({"error": "Username and a password of at least 6 characters are required"}), 400
    if user["role"] == "admin" and role != "user":
        return jsonify({"error": "Admin logins can only create User logins"}), 403
    if role not in ("admin", "user"):
        return jsonify({"error": "Invalid role"}), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        return jsonify({"error": "That username is already taken"}), 400

    # Which main companies the new login is attached to. An admin's users
    # always inherit exactly the admin's own assignment; a super admin picks.
    if user["role"] == "admin":
        main_ids = assigned_main_ids(user)
    else:
        requested = body.get("mainIds") or []
        valid = set(all_main_ids())
        main_ids = [m for m in requested if m in valid]
        if not main_ids:
            return jsonify({"error": "Choose at least one main company for this login"}), 400

    if role == "admin" or not any(m in body for m in MODULES):
        mods = {m: True for m in MODULES}
    else:
        mods = {m: bool(body.get(m)) for m in MODULES}

    db.execute(
        "INSERT INTO users (username,password_hash,role,created_by,active,created_at,"
        "can_employees,can_attendance,can_payslip,can_reports) VALUES (?,?,?,?,1,?,?,?,?,?)",
        (username, generate_password_hash(password), role, user["id"], now_iso(),
         mods["employees"], mods["attendance"], mods["payslip"], mods["reports"]),
    )
    new_id = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    for mid in main_ids:
        db.execute("INSERT OR IGNORE INTO user_mains (user_id, main_id) VALUES (?,?)", (new_id, mid))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (new_id,)).fetchone()
    return jsonify(_user_json(row, db))


def _managed_user_or_403(user, target_id, db):
    target = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    if target is None:
        return None
    if user["role"] == "super_admin":
        return target
    if user["role"] == "admin" and target["created_by"] == user["id"] and target["role"] == "user":
        return target
    return None


@app.route("/api/users/<int:target_id>", methods=["PUT"])
@login_required(roles=("super_admin", "admin"))
def users_update(target_id):
    user = g.current_user
    db = get_db()
    target = _managed_user_or_403(user, target_id, db)
    if target is None:
        return jsonify({"error": "Not found or not permitted"}), 404
    body = request.get_json(silent=True) or {}
    if "active" in body:
        db.execute("UPDATE users SET active=? WHERE id=?", (1 if body["active"] else 0, target_id))
    if "password" in body and body["password"]:
        if len(body["password"]) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(body["password"]), target_id))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    return jsonify(_user_json(row, db))


@app.route("/api/users/<int:target_id>/modules", methods=["POST"])
@login_required(roles=("super_admin", "admin"))
def users_set_modules(target_id):
    """Set which modules a login may use across the companies in its scope."""
    user = g.current_user
    db = get_db()
    target = _managed_user_or_403(user, target_id, db)
    if target is None:
        return jsonify({"error": "Not found or not permitted"}), 404
    if target["role"] in ("super_admin", "admin"):
        return jsonify({"error": "Admin logins already hold every module"}), 400
    body = request.get_json(silent=True) or {}
    mods = {m: bool(body.get(m)) for m in MODULES}
    db.execute(
        "UPDATE users SET can_employees=?, can_attendance=?, can_payslip=?, can_reports=? WHERE id=?",
        (mods["employees"], mods["attendance"], mods["payslip"], mods["reports"], target_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    return jsonify(_user_json(row, db))


@app.route("/api/users/<int:target_id>/mains", methods=["POST"])
@login_required(roles=("super_admin",))
def users_set_mains(target_id):
    """Assign which main companies a login works under (Super Admin only)."""
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    if target is None or target["role"] == "super_admin":
        return jsonify({"error": "Not found or not permitted"}), 404
    body = request.get_json(silent=True) or {}
    valid = set(all_main_ids())
    main_ids = [m for m in (body.get("mainIds") or []) if m in valid]
    if not main_ids:
        return jsonify({"error": "Choose at least one main company"}), 400

    db.execute("DELETE FROM user_mains WHERE user_id=?", (target_id,))
    for mid in main_ids:
        db.execute("INSERT OR IGNORE INTO user_mains (user_id, main_id) VALUES (?,?)", (target_id, mid))

    # Keep an admin's own user logins in step with the admin's assignment.
    if target["role"] == "admin":
        for child in db.execute(
            "SELECT id FROM users WHERE created_by=? AND role='user'", (target_id,)
        ).fetchall():
            db.execute("DELETE FROM user_mains WHERE user_id=?", (child["id"],))
            for mid in main_ids:
                db.execute("INSERT OR IGNORE INTO user_mains (user_id, main_id) VALUES (?,?)", (child["id"], mid))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    return jsonify(_user_json(row, db))


# ---------------------------------------------------------------------------
# Data files (Super Admin) - see and delete uploaded employee/attendance blobs
# ---------------------------------------------------------------------------
@app.route("/api/data-files", methods=["GET"])
@login_required(roles=("super_admin",))
def data_files_list():
    db = get_db()
    all_companies = {c["id"]: c for c in db.execute("SELECT * FROM companies").fetchall()}
    main_names = {c["id"]: c["name"] for c in all_companies.values() if c["is_main"]}

    out = []
    for r in db.execute("SELECT key, value, updated_at FROM kv_store ORDER BY updated_at DESC").fetchall():
        module, company_id = _parse_storage_key(r["key"])
        if module is None:
            continue
        company = all_companies.get(company_id)
        try:
            record_count = len(json.loads(r["value"]))
        except Exception:
            record_count = None
        month = r["key"].rsplit("_", 1)[-1] if module == "attendance" else None
        out.append({
            "key": r["key"],
            "module": module,
            "month": month,
            "companyId": company_id,
            "companyName": company["name"] if company else f"(deleted company {company_id})",
            "isMain": bool(company["is_main"]) if company else False,
            "mainName": None if not company or company["is_main"] else main_names.get(company["parent_id"]),
            "recordCount": record_count,
            "updatedAt": r["updated_at"],
        })
    return jsonify(out)


# ---------------------------------------------------------------------------
# Storage API (employees_<companyId>, monthly_<companyId>_<month>)
# ---------------------------------------------------------------------------
def _parse_storage_key(key):
    if key.startswith("employees_"):
        return "employees", key[len("employees_"):]
    if key.startswith("monthly_"):
        rest = key[len("monthly_"):]
        company_id, _, _month = rest.rpartition("_")
        return "attendance", company_id
    return None, None


@app.route("/api/storage/<path:key>", methods=["GET"])
@login_required()
def storage_get(key):
    user = g.current_user
    module, company_id = _parse_storage_key(key)
    if module is None:
        return jsonify({"error": "not found", "key": key}), 404
    company = get_company(company_id)
    if company is None:
        return jsonify({"error": "not found", "key": key}), 404
    allowed = can_read_employees(user, company) if module == "employees" else can_read_attendance(user, company)
    if not allowed:
        return jsonify({"error": "forbidden"}), 403

    db = get_db()
    row = db.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    if row is None:
        return jsonify({"error": "not found", "key": key}), 404
    return jsonify({"key": key, "value": row["value"]})


@app.route("/api/storage/<path:key>", methods=["PUT"])
@login_required()
def storage_set(key):
    user = g.current_user
    module, company_id = _parse_storage_key(key)
    if module is None:
        return jsonify({"error": "Unrecognised storage key"}), 400
    company = get_company(company_id)
    if company is None:
        return jsonify({"error": "Unknown company"}), 400
    allowed = can_write_employees(user, company) if module == "employees" else can_write_attendance(user, company)
    if not allowed:
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    value = body.get("value")
    if value is None:
        return jsonify({"error": "'value' is required"}), 400
    db = get_db()
    now = now_iso()
    db.execute(
        """
        INSERT INTO kv_store (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now),
    )
    db.commit()
    return jsonify({"key": key, "value": value, "updated_at": now})


@app.route("/api/storage/<path:key>", methods=["DELETE"])
@login_required()
def storage_delete(key):
    user = g.current_user
    module, company_id = _parse_storage_key(key)
    if module is None:
        return jsonify({"error": "Unrecognised storage key"}), 400
    company = get_company(company_id)
    if company is None:
        return jsonify({"error": "Unknown company"}), 400
    allowed = can_write_employees(user, company) if module == "employees" else can_write_attendance(user, company)
    if not allowed:
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    db.execute("DELETE FROM kv_store WHERE key = ?", (key,))
    db.commit()
    return jsonify({"key": key, "deleted": True})


# ---------------------------------------------------------------------------
# Raw JSON export - always limited to what the login may see
# ---------------------------------------------------------------------------
@app.route("/api/export", methods=["GET"])
@login_required()
def export_all():
    """
    Raw JSON snapshot of the payroll blobs this login can reach.

        ?company=<id>   just that one company
        ?scope=mains    main companies only (Super Admin)
        ?scope=clients  client companies only
    """
    user = g.current_user
    db = get_db()
    company_id = (request.args.get("company") or "").strip()
    scope = (request.args.get("scope") or "").strip()

    if company_id:
        company = get_company(company_id)
        if company is None:
            return jsonify({"error": "Unknown company"}), 404
        if not in_scope(user, company):
            return jsonify({"error": "forbidden"}), 403
        wanted = {company_id}
    else:
        rows = workable_companies(user)
        if scope == "mains":
            rows = [c for c in rows if c["is_main"]]
        elif scope == "clients":
            rows = [c for c in rows if not c["is_main"]]
        wanted = {c["id"] for c in rows}

    out = {}
    for r in db.execute("SELECT key, value FROM kv_store ORDER BY key").fetchall():
        _mod, cid = _parse_storage_key(r["key"])
        if cid in wanted:
            out[r["key"]] = json.loads(r["value"])
    return jsonify(out)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)

if not os.path.exists(DB_PATH):
    init_db()
