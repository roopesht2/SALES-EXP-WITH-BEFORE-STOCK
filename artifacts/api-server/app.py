import os
import json
import sqlite3
import functools
from datetime import date, timedelta

from flask import Flask, request, jsonify, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tracker.db")

app = Flask(__name__)

_default_secret_file = os.path.join(BASE_DIR, ".tracker_secret")
def _load_or_create_secret():
    env = os.environ.get("TRACKER_SECRET") or os.environ.get("SESSION_SECRET")
    if env:
        return env
    try:
        if os.path.exists(_default_secret_file):
            with open(_default_secret_file, "r") as fh:
                existing = fh.read().strip()
                if existing:
                    return existing
        import secrets as _secrets
        new_secret = _secrets.token_hex(32)
        with open(_default_secret_file, "w") as fh:
            fh.write(new_secret)
        try:
            os.chmod(_default_secret_file, 0o600)
        except OSError:
            pass
        return new_secret
    except OSError:
        return "please-change-this-secret-key"

app.secret_key = _load_or_create_secret()
app.permanent_session_lifetime = timedelta(days=30)

COMPANY = {
    "name": "SHREE BALAJI ASSOCIATES",
    "address": "Near Om Sai Biofuels Bargawan Odgadi Distt. Singrauli",
    "gstin": "23AEPFS7841N1Z9",
}

KNOWN_ENTRY_FIELDS = {
    "date", "kind", "client", "vendor", "item_type", "itemType", "qty", "rate",
    "expense_type", "expenseType", "amount", "note", "method", "received",
    "received_date", "receivedDate", "linked_sale_id", "linkedSaleId",
    "created_by", "created_at", "id", "bank_account_id", "bankAccountId",
}

DEFAULT_BANK_NAME = "Main Bank"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _has_col(conn, table, col):
    return col in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            label TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            kind TEXT NOT NULL,
            client TEXT,
            vendor TEXT,
            item_type TEXT,
            qty REAL,
            rate REAL,
            expense_type TEXT,
            amount REAL NOT NULL,
            note TEXT,
            received INTEGER NOT NULL DEFAULT 0,
            method TEXT,
            received_date TEXT,
            linked_sale_id INTEGER,
            extra_json TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            method TEXT NOT NULL,
            amount REAL NOT NULL,
            loading_unloading REAL NOT NULL DEFAULT 0,
            loading_unloading_expense_id INTEGER,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS advances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            client TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            note TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS client_adjustments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            client TEXT NOT NULL,
            adj_type TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bank_transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT,
            note TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bank_accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            opening_balance REAL NOT NULL DEFAULT 0,
            opening_date TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    for col, coltype in (
        ("item_type", "TEXT"), ("qty", "REAL"), ("rate", "REAL"),
        ("expense_type", "TEXT"), ("linked_sale_id", "INTEGER"),
        ("extra_json", "TEXT"), ("vendor", "TEXT"),
    ):
        if not _has_col(conn, "entries", col):
            conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {coltype}")

    for table in ("entries", "payments", "advances", "bank_transactions"):
        if not _has_col(conn, table, "bank_account_id"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN bank_account_id INTEGER")

    default_row = conn.execute("SELECT id FROM bank_accounts ORDER BY id LIMIT 1").fetchone()
    if default_row is None:
        ob = conn.execute("SELECT value FROM settings WHERE key='opening_bank_balance'").fetchone()
        od = conn.execute("SELECT value FROM settings WHERE key='opening_bank_date'").fetchone()
        cur = conn.execute(
            "INSERT INTO bank_accounts(name, opening_balance, opening_date) VALUES (?,?,?)",
            (
                DEFAULT_BANK_NAME,
                float(ob["value"]) if ob and ob["value"] is not None else 0,
                od["value"] if od else None,
            ),
        )
        default_account_id = cur.lastrowid
    else:
        default_account_id = default_row["id"]

    conn.execute(
        "UPDATE payments SET bank_account_id=? WHERE method='bank' AND bank_account_id IS NULL",
        (default_account_id,),
    )
    conn.execute(
        "UPDATE advances SET bank_account_id=? WHERE method='bank' AND bank_account_id IS NULL",
        (default_account_id,),
    )
    conn.execute(
        "UPDATE entries SET bank_account_id=? WHERE kind='expense' AND method='bank' AND bank_account_id IS NULL",
        (default_account_id,),
    )
    conn.execute(
        "UPDATE bank_transactions SET bank_account_id=? WHERE bank_account_id IS NULL",
        (default_account_id,),
    )

    old_paid_sales = conn.execute(
        """SELECT e.* FROM entries e
           WHERE e.kind='sale' AND e.received=1
           AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.entry_id = e.id)"""
    ).fetchall()
    for e in old_paid_sales:
        conn.execute(
            """INSERT INTO payments(entry_id, date, method, amount, created_by, bank_account_id)
               VALUES (?,?,?,?,?,?)""",
            (
                e["id"], e["received_date"] or e["date"], e["method"] or "cash",
                e["amount"], e["created_by"] or "migration",
                default_account_id if (e["method"] == "bank") else None,
            ),
        )

    if first_time:
        conn.execute(
            "INSERT INTO users VALUES (?,?,?,?)",
            ("admin", generate_password_hash("12346"), "admin", "Admin"),
        )
        conn.execute(
            "INSERT INTO users VALUES (?,?,?,?)",
            ("user", generate_password_hash("1234"), "operator", "Data Entry Operator"),
        )
        print("First run: created default users admin/12346 and user/1234.")
    conn.commit()
    conn.close()


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return jsonify({"error": "not authenticated"}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "admin only"}), 403
        return f(*args, **kwargs)
    return wrapper


def is_admin():
    return session.get("role") == "admin"


def check_date_window(entry_date, earliest_allowed=None):
    if is_admin():
        return None
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    lo = max(earliest_allowed, yesterday) if earliest_allowed else yesterday
    if entry_date < lo or entry_date > today:
        return f"date must be between {lo} and {today}"
    return None


def split_known_extra(d, known):
    extra = {k: v for k, v in d.items() if k not in known}
    return json.dumps(extra) if extra else None


def merge_extra(row_dict):
    extra = row_dict.pop("extra_json", None)
    if extra:
        try:
            for k, v in json.loads(extra).items():
                if k not in row_dict:
                    row_dict[k] = v
        except (TypeError, ValueError):
            pass
    return row_dict


def resolve_bank_account_id(conn, requested_id):
    if requested_id is not None:
        try:
            requested_id = int(requested_id)
        except (TypeError, ValueError):
            requested_id = None
    if requested_id is not None:
        row = conn.execute(
            "SELECT id FROM bank_accounts WHERE id=? AND is_active=1", (requested_id,)
        ).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM bank_accounts WHERE is_active=1 ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/company")
def company_info():
    return jsonify(COMPANY)


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401
    session.permanent = True
    session["username"] = row["username"]
    session["role"] = row["role"]
    return jsonify({"username": row["username"], "role": row["role"], "label": row["label"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "username" not in session:
        return jsonify({"error": "not authenticated"}), 401
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (session["username"],)).fetchone()
    conn.close()
    if not row:
        session.clear()
        return jsonify({"error": "not authenticated"}), 401
    return jsonify({"username": row["username"], "role": row["role"], "label": row["label"]})


@app.route("/api/item-types")
@login_required
def list_item_types():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT item_type FROM entries WHERE item_type IS NOT NULL AND item_type != '' ORDER BY item_type"
    ).fetchall()
    conn.close()
    return jsonify([r["item_type"] for r in rows])


@app.route("/api/expense-types")
@login_required
def list_expense_types():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT expense_type FROM entries WHERE expense_type IS NOT NULL AND expense_type != '' ORDER BY expense_type"
    ).fetchall()
    conn.close()
    return jsonify([r["expense_type"] for r in rows])


@app.route("/api/adjustment-types")
@login_required
def list_adjustment_types():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT adj_type FROM client_adjustments ORDER BY adj_type"
    ).fetchall()
    conn.close()
    defaults = ["Discount", "Round Off", "Bad Debt", "TDS"]
    seen = [r["adj_type"] for r in rows]
    merged = defaults + [s for s in seen if s not in defaults]
    return jsonify(merged)


@app.route("/api/clients")
@login_required
def list_clients():
    conn = get_db()
    names = set()
    for row in conn.execute("SELECT DISTINCT client FROM entries WHERE client IS NOT NULL AND client != ''"):
        names.add(row["client"])
    for row in conn.execute("SELECT DISTINCT client FROM advances"):
        names.add(row["client"])
    for row in conn.execute("SELECT DISTINCT client FROM client_adjustments"):
        names.add(row["client"])
    conn.close()
    return jsonify(sorted(names))


@app.route("/api/vendors")
@login_required
def list_vendors():
    conn = get_db()
    names = set()
    for row in conn.execute("SELECT DISTINCT vendor FROM entries WHERE vendor IS NOT NULL AND vendor != ''"):
        names.add(row["vendor"])
    conn.close()
    return jsonify(sorted(names))


@app.route("/api/entries", methods=["GET"])
@login_required
def list_entries():
    conn = get_db()
    rows = [merge_extra(dict(r)) for r in conn.execute("SELECT * FROM entries ORDER BY id").fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/entries", methods=["POST"])
@login_required
def add_entry():
    d = request.get_json(force=True) or {}
    kind = d.get("kind")
    entry_date = d.get("date")
    if not entry_date:
        return jsonify({"error": "date is required"}), 400

    err = check_date_window(entry_date)
    if err:
        return jsonify({"error": err}), 400

    if kind == "sale":
        try:
            qty = float(d.get("qty", 0))
            rate = float(d.get("rate", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid quantity or rate"}), 400
        if qty <= 0 or rate <= 0:
            return jsonify({"error": "quantity and rate must be positive"}), 400
        if not (d.get("itemType") or "").strip():
            return jsonify({"error": "item type is required"}), 400
        if not (d.get("client") or "").strip():
            return jsonify({"error": "client is required"}), 400
        amount = round(qty * rate, 2)
        item_type = d.get("itemType", "").strip()
        expense_type = None
        vendor = None
    elif kind == "purchase":
        try:
            qty = float(d.get("qty", 0))
            rate = float(d.get("rate", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid quantity or rate"}), 400
        if qty <= 0 or rate <= 0:
            return jsonify({"error": "quantity and rate must be positive"}), 400
        if not (d.get("itemType") or "").strip():
            return jsonify({"error": "item type is required"}), 400
        if not (d.get("vendor") or "").strip():
            return jsonify({"error": "vendor is required"}), 400
        amount = round(qty * rate, 2)
        item_type = d.get("itemType", "").strip()
        expense_type = None
        vendor = d.get("vendor", "").strip()
    elif kind == "expense":
        qty = None
        rate = None
        item_type = None
        vendor = None
        if not (d.get("expenseType") or "").strip():
            return jsonify({"error": "expense type is required"}), 400
        expense_type = d.get("expenseType", "").strip()
        if d.get("method") not in ("cash", "bank"):
            return jsonify({"error": "method must be cash or bank"}), 400
        try:
            amount = float(d.get("amount", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid amount"}), 400
        if amount <= 0:
            return jsonify({"error": "amount must be positive"}), 400
    else:
        return jsonify({"error": "kind must be sale, purchase, or expense"}), 400

    conn = get_db()
    bank_account_id = None
    if kind == "expense" and d.get("method") == "bank":
        bank_account_id = resolve_bank_account_id(conn, d.get("bankAccountId"))
        if not bank_account_id:
            conn.close()
            return jsonify({"error": "no active bank account; add one first"}), 400

    cur = conn.execute(
        """INSERT INTO entries(date, kind, client, vendor, item_type, qty, rate, expense_type, amount, note, method, created_by, bank_account_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            entry_date, kind, d.get("client", ""), vendor, item_type, qty, rate,
            expense_type, amount, d.get("note", ""),
            d.get("method") if kind == "expense" else None,
            session["username"], bank_account_id,
        ),
    )
    new_id = cur.lastrowid

    payment_id = None
    if kind == "sale" and d.get("received"):
        method = d.get("method")
        if method not in ("cash", "bank", "advance"):
            conn.close()
            return jsonify({"error": "method must be cash, bank, or advance"}), 400
        pay_err, payment_id = _record_payment(
            conn, new_id, entry_date, method, amount,
            d.get("loadingUnloadingAmount"), d.get("loadingUnloadingMethod"),
            session["username"], d.get("bankAccountId"),
        )
        if pay_err:
            conn.rollback()
            conn.close()
            return jsonify({"error": pay_err}), 400

    if kind == "purchase" and d.get("paid"):
        method = d.get("method")
        if method not in ("cash", "bank"):
            conn.close()
            return jsonify({"error": "method must be cash or bank"}), 400
        pay_err, payment_id = _record_purchase_payment(
            conn, new_id, entry_date, method, amount,
            session["username"], d.get("bankAccountId"),
        )
        if pay_err:
            conn.rollback()
            conn.close()
            return jsonify({"error": pay_err}), 400

    conn.commit()
    conn.close()
    return jsonify({"id": new_id, "amount": amount, "paymentId": payment_id})


def _client_advance_balance(conn, client):
    received = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM advances WHERE client=?", (client,)
    ).fetchone()["s"]
    used = conn.execute(
        """SELECT COALESCE(SUM(p.amount),0) s FROM payments p
           JOIN entries e ON e.id = p.entry_id
           WHERE e.client=? AND p.method='advance'""",
        (client,),
    ).fetchone()["s"]
    return received - used


def _record_payment(conn, entry_id, pay_date, method, amount,
                    loading_amt, loading_method, username, bank_account_id=None):
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry or entry["kind"] != "sale":
        return "sale not found", None
    if amount is None or amount <= 0:
        return "amount must be positive", None
    if method not in ("cash", "bank", "advance"):
        return "method must be cash, bank, or advance", None
    if method == "advance":
        balance = _client_advance_balance(conn, entry["client"])
        if amount > balance + 0.005:
            return f"insufficient advance balance ({balance:.2f} available)", None

    resolved_bank_id = None
    if method == "bank":
        resolved_bank_id = resolve_bank_account_id(conn, bank_account_id)
        if not resolved_bank_id:
            return "no active bank account; add one first", None

    loading_expense_id = None
    loading_amt = float(loading_amt) if loading_amt not in (None, "") else 0
    if loading_amt < 0:
        return "loading/unloading amount cannot be negative", None
    if loading_amt > 0:
        if loading_method not in ("cash", "bank"):
            return "loading/unloading method must be cash or bank", None
        loading_bank_id = None
        if loading_method == "bank":
            loading_bank_id = resolved_bank_id or resolve_bank_account_id(conn, bank_account_id)
            if not loading_bank_id:
                return "no active bank account for loading/unloading", None
        cur = conn.execute(
            """INSERT INTO entries(date, kind, client, expense_type, amount, note, method, linked_sale_id, created_by, bank_account_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                pay_date, "expense", entry["client"], "Loading/Unloading", loading_amt,
                f"Loading/unloading for sale #{entry_id} ({entry['item_type'] or ''} to {entry['client']})".strip(),
                loading_method, entry_id, username, loading_bank_id,
            ),
        )
        loading_expense_id = cur.lastrowid

    cur = conn.execute(
        """INSERT INTO payments(entry_id, date, method, amount, loading_unloading, loading_unloading_expense_id, created_by, bank_account_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (entry_id, pay_date, method, amount, loading_amt, loading_expense_id, username, resolved_bank_id),
    )
    return None, cur.lastrowid


def _record_purchase_payment(conn, entry_id, pay_date, method, amount, username, bank_account_id=None):
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if not entry or entry["kind"] != "purchase":
        return "purchase not found", None
    if amount is None or amount <= 0:
        return "amount must be positive", None
    if method not in ("cash", "bank"):
        return "method must be cash or bank", None
    resolved_bank_id = None
    if method == "bank":
        resolved_bank_id = resolve_bank_account_id(conn, bank_account_id)
        if not resolved_bank_id:
            return "no active bank account; add one first", None
    cur = conn.execute(
        """INSERT INTO payments(entry_id, date, method, amount, loading_unloading, created_by, bank_account_id)
           VALUES (?,?,?,?,?,?,?)""",
        (entry_id, pay_date, method, amount, 0, username, resolved_bank_id),
    )
    return None, cur.lastrowid


@app.route("/api/entries/<int:eid>/pay", methods=["POST"])
@login_required
def pay_entry(eid):
    d = request.get_json(force=True) or {}
    pay_date = d.get("date")
    if not pay_date:
        return jsonify({"error": "date is required"}), 400

    conn = get_db()
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
    if not entry or entry["kind"] != "sale":
        conn.close()
        return jsonify({"error": "sale not found"}), 404

    err = check_date_window(pay_date, earliest_allowed=entry["date"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400

    try:
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"error": "invalid amount"}), 400

    pay_err, payment_id = _record_payment(
        conn, eid, pay_date, d.get("method"), amount,
        d.get("loadingUnloadingAmount"), d.get("loadingUnloadingMethod"),
        session["username"], d.get("bankAccountId"),
    )
    if pay_err:
        conn.rollback()
        conn.close()
        return jsonify({"error": pay_err}), 400

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "paymentId": payment_id})


@app.route("/api/purchases/<int:eid>/pay", methods=["POST"])
@login_required
def pay_purchase(eid):
    d = request.get_json(force=True) or {}
    pay_date = d.get("date")
    if not pay_date:
        return jsonify({"error": "date is required"}), 400

    conn = get_db()
    entry = conn.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
    if not entry or entry["kind"] != "purchase":
        conn.close()
        return jsonify({"error": "purchase not found"}), 404

    err = check_date_window(pay_date, earliest_allowed=entry["date"])
    if err:
        conn.close()
        return jsonify({"error": err}), 400

    try:
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"error": "invalid amount"}), 400

    pay_err, payment_id = _record_purchase_payment(
        conn, eid, pay_date, d.get("method"), amount,
        session["username"], d.get("bankAccountId"),
    )
    if pay_err:
        conn.rollback()
        conn.close()
        return jsonify({"error": pay_err}), 400

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "paymentId": payment_id})


@app.route("/api/payments")
@login_required
def list_payments():
    conn = get_db()
    rows = conn.execute("SELECT * FROM payments ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/payments/<int:pid>", methods=["DELETE"])
@login_required
@admin_required
def delete_payment(pid):
    conn = get_db()
    row = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404
    if row["loading_unloading_expense_id"]:
        conn.execute("DELETE FROM entries WHERE id=?", (row["loading_unloading_expense_id"],))
    conn.execute("DELETE FROM payments WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/entries/<int:eid>", methods=["DELETE"])
@login_required
@admin_required
def delete_entry(eid):
    conn = get_db()
    linked = conn.execute("SELECT id FROM entries WHERE linked_sale_id=?", (eid,)).fetchall()
    for row in linked:
        conn.execute("DELETE FROM payments WHERE loading_unloading_expense_id=?", (row["id"],))
        conn.execute("DELETE FROM entries WHERE id=?", (row["id"],))
    conn.execute("DELETE FROM payments WHERE entry_id=?", (eid,))
    conn.execute("DELETE FROM entries WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/advances", methods=["GET"])
@login_required
def list_advances():
    conn = get_db()
    rows = conn.execute("SELECT * FROM advances ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/advances", methods=["POST"])
@login_required
def add_advance():
    d = request.get_json(force=True) or {}
    if not (d.get("client") or "").strip():
        return jsonify({"error": "client is required"}), 400
    if d.get("method") not in ("cash", "bank"):
        return jsonify({"error": "method must be cash or bank"}), 400
    try:
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400
    adv_date = d.get("date")
    if not adv_date:
        return jsonify({"error": "date is required"}), 400
    err = check_date_window(adv_date)
    if err:
        return jsonify({"error": err}), 400

    conn = get_db()
    bank_account_id = None
    if d.get("method") == "bank":
        bank_account_id = resolve_bank_account_id(conn, d.get("bankAccountId"))
        if not bank_account_id:
            conn.close()
            return jsonify({"error": "no active bank account; add one first"}), 400

    cur = conn.execute(
        "INSERT INTO advances(date, client, amount, method, note, created_by, bank_account_id) VALUES (?,?,?,?,?,?,?)",
        (adv_date, d.get("client").strip(), amount, d.get("method"), d.get("note", ""), session["username"], bank_account_id),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"id": new_id})


@app.route("/api/advances/<int:aid>", methods=["DELETE"])
@login_required
@admin_required
def delete_advance(aid):
    conn = get_db()
    conn.execute("DELETE FROM advances WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client-adjustments", methods=["GET"])
@login_required
def list_adjustments():
    conn = get_db()
    rows = conn.execute("SELECT * FROM client_adjustments ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/client-adjustments", methods=["POST"])
@login_required
def add_adjustment():
    d = request.get_json(force=True) or {}
    if not (d.get("client") or "").strip():
        return jsonify({"error": "client is required"}), 400
    if not (d.get("adjType") or "").strip():
        return jsonify({"error": "adjustment type is required"}), 400
    try:
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400
    adj_date = d.get("date")
    if not adj_date:
        return jsonify({"error": "date is required"}), 400
    err = check_date_window(adj_date)
    if err:
        return jsonify({"error": err}), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO client_adjustments(date, client, adj_type, amount, note, created_by) VALUES (?,?,?,?,?,?)",
        (adj_date, d.get("client").strip(), d.get("adjType").strip(), amount, d.get("note", ""), session["username"]),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"id": new_id})


@app.route("/api/client-adjustments/<int:aid>", methods=["DELETE"])
@login_required
@admin_required
def delete_adjustment(aid):
    conn = get_db()
    conn.execute("DELETE FROM client_adjustments WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/bank-accounts", methods=["GET"])
@login_required
def list_bank_accounts():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, opening_balance, opening_date, is_active FROM bank_accounts ORDER BY is_active DESC, id"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/bank-accounts", methods=["POST"])
@login_required
@admin_required
def add_bank_account():
    d = request.get_json(force=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        ob = float(d.get("openingBalance", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid opening balance"}), 400
    ob_date = d.get("openingDate") or None
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO bank_accounts(name, opening_balance, opening_date) VALUES (?,?,?)",
            (name, ob, ob_date),
        )
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "an account with that name already exists"}), 400
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"id": new_id})


@app.route("/api/bank-accounts/<int:aid>", methods=["PATCH"])
@login_required
@admin_required
def update_bank_account(aid):
    d = request.get_json(force=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (aid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404
    fields, values = [], []
    if "name" in d:
        name = (d["name"] or "").strip()
        if not name:
            conn.close(); return jsonify({"error": "name cannot be blank"}), 400
        fields.append("name=?"); values.append(name)
    if "openingBalance" in d:
        try:
            fields.append("opening_balance=?"); values.append(float(d["openingBalance"] or 0))
        except (TypeError, ValueError):
            conn.close(); return jsonify({"error": "invalid opening balance"}), 400
    if "openingDate" in d:
        fields.append("opening_date=?"); values.append(d["openingDate"] or None)
    if "isActive" in d:
        fields.append("is_active=?"); values.append(1 if d["isActive"] else 0)
    if not fields:
        conn.close(); return jsonify({"error": "nothing to update"}), 400
    values.append(aid)
    try:
        conn.execute(f"UPDATE bank_accounts SET {', '.join(fields)} WHERE id=?", values)
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "an account with that name already exists"}), 400
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/bank-accounts/<int:aid>", methods=["DELETE"])
@login_required
@admin_required
def delete_bank_account(aid):
    conn = get_db()
    used = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM payments WHERE bank_account_id=?) +
             (SELECT COUNT(*) FROM advances WHERE bank_account_id=?) +
             (SELECT COUNT(*) FROM entries WHERE bank_account_id=?) +
             (SELECT COUNT(*) FROM bank_transactions WHERE bank_account_id=?) c""",
        (aid, aid, aid, aid),
    ).fetchone()["c"]
    if used:
        conn.close()
        return jsonify({"error": "this account has transactions; deactivate it instead"}), 400
    conn.execute("DELETE FROM bank_accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


BANK_TXN_TYPES = ("deposit", "withdrawal", "cash_to_bank", "bank_to_cash")


@app.route("/api/bank-transactions", methods=["GET"])
@login_required
def list_bank_transactions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM bank_transactions ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/bank-transactions", methods=["POST"])
@login_required
def add_bank_transaction():
    d = request.get_json(force=True) or {}
    if d.get("type") not in BANK_TXN_TYPES:
        return jsonify({"error": "type must be one of " + ", ".join(BANK_TXN_TYPES)}), 400
    try:
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400
    txn_date = d.get("date")
    if not txn_date:
        return jsonify({"error": "date is required"}), 400
    err = check_date_window(txn_date)
    if err:
        return jsonify({"error": err}), 400

    conn = get_db()
    bank_account_id = resolve_bank_account_id(conn, d.get("bankAccountId"))
    if not bank_account_id:
        conn.close()
        return jsonify({"error": "no active bank account; add one first"}), 400

    cur = conn.execute(
        "INSERT INTO bank_transactions(date, type, amount, category, note, created_by, bank_account_id) VALUES (?,?,?,?,?,?,?)",
        (txn_date, d.get("type"), amount, d.get("category", ""), d.get("note", ""), session["username"], bank_account_id),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"id": new_id})


@app.route("/api/bank-transactions/<int:tid>", methods=["DELETE"])
@login_required
@admin_required
def delete_bank_transaction(tid):
    conn = get_db()
    conn.execute("DELETE FROM bank_transactions WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/bank-transaction-types")
@login_required
def list_bank_transaction_types():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM bank_transactions WHERE category IS NOT NULL AND category != '' ORDER BY category"
    ).fetchall()
    conn.close()
    defaults = ["Bank Charges", "Interest Earned", "Owner Capital", "Cheque Bounce Reversal"]
    seen = [r["category"] for r in rows]
    return jsonify(defaults + [s for s in seen if s not in defaults])


@app.route("/api/export")
@login_required
@admin_required
def export_data():
    conn = get_db()
    entries = [merge_extra(dict(r)) for r in conn.execute("SELECT * FROM entries ORDER BY id").fetchall()]
    payments = [dict(r) for r in conn.execute("SELECT * FROM payments ORDER BY id").fetchall()]
    advances = [dict(r) for r in conn.execute("SELECT * FROM advances ORDER BY id").fetchall()]
    adjustments = [dict(r) for r in conn.execute("SELECT * FROM client_adjustments ORDER BY id").fetchall()]
    bank_txns = [dict(r) for r in conn.execute("SELECT * FROM bank_transactions ORDER BY id").fetchall()]
    bank_accounts = [dict(r) for r in conn.execute(
        "SELECT id, name, opening_balance, opening_date, is_active FROM bank_accounts ORDER BY id"
    ).fetchall()]
    settings_rows = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}
    conn.close()
    return jsonify({
        "schemaVersion": 5,
        "company": COMPANY,
        "entries": entries,
        "payments": payments,
        "advances": advances,
        "clientAdjustments": adjustments,
        "bankTransactions": bank_txns,
        "bankAccounts": bank_accounts,
        "settings": settings_rows,
    })


@app.route("/api/import", methods=["POST"])
@login_required
@admin_required
def import_data():
    data = request.get_json(force=True)
    if data is None:
        return jsonify({"error": "invalid backup format"}), 400

    if isinstance(data, list):
        entry_rows, payment_rows, advance_rows = data, None, []
        adj_rows, bank_rows, settings_data, account_rows = [], [], {}, []
    else:
        entry_rows = data.get("entries", [])
        payment_rows = data.get("payments")
        advance_rows = data.get("advances", [])
        adj_rows = data.get("clientAdjustments", data.get("client_adjustments", []))
        bank_rows = data.get("bankTransactions", data.get("bank_transactions", []))
        settings_data = data.get("settings", {})
        account_rows = data.get("bankAccounts", data.get("bank_accounts", []))

    if not isinstance(entry_rows, list):
        return jsonify({"error": "invalid backup format"}), 400

    conn = get_db()
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM advances")
    conn.execute("DELETE FROM client_adjustments")
    conn.execute("DELETE FROM bank_transactions")
    conn.execute("DELETE FROM bank_accounts")

    account_id_map = {}
    if account_rows:
        for a in account_rows:
            try:
                ob = float(a.get("opening_balance", a.get("openingBalance", 0)) or 0)
            except (TypeError, ValueError):
                ob = 0
            cur = conn.execute(
                "INSERT INTO bank_accounts(name, opening_balance, opening_date, is_active) VALUES (?,?,?,?)",
                (
                    a.get("name") or DEFAULT_BANK_NAME,
                    ob,
                    a.get("opening_date") or a.get("openingDate") or None,
                    1 if a.get("is_active", a.get("isActive", 1)) else 0,
                ),
            )
            account_id_map[a.get("id")] = cur.lastrowid
    if not account_id_map:
        cur = conn.execute(
            "INSERT INTO bank_accounts(name, opening_balance, opening_date) VALUES (?,?,?)",
            (
                DEFAULT_BANK_NAME,
                float(settings_data.get("opening_bank_balance", 0) or 0) if settings_data else 0,
                settings_data.get("opening_bank_date") if settings_data else None,
            ),
        )
        account_id_map[None] = cur.lastrowid
    fallback_account_id = next(iter(account_id_map.values()))

    def map_account(old_id):
        if old_id is None:
            return None
        return account_id_map.get(old_id, fallback_account_id)

    id_map = {}
    for r in entry_rows:
        try:
            amount = float(r.get("amount", 0))
        except (TypeError, ValueError):
            continue
        qty = r.get("qty")
        rate = r.get("rate")
        try:
            qty = float(qty) if qty not in (None, "") else None
            rate = float(rate) if rate not in (None, "") else None
        except (TypeError, ValueError):
            qty, rate = None, None
        old_id = r.get("id")
        extra = split_known_extra(r, KNOWN_ENTRY_FIELDS)
        old_bank_id = r.get("bank_account_id") or r.get("bankAccountId")
        new_bank_id = map_account(old_bank_id) if r.get("method") == "bank" and r.get("kind") == "expense" else None
        cur = conn.execute(
            """INSERT INTO entries(date, kind, client, vendor, item_type, qty, rate, expense_type, amount, note, method, received, received_date, extra_json, created_by, bank_account_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("date"), r.get("kind"), r.get("client", ""), r.get("vendor", ""),
                r.get("item_type") or r.get("itemType"), qty, rate,
                r.get("expense_type") or r.get("expenseType"), amount,
                r.get("note", ""), r.get("method"),
                1 if r.get("received") else 0,
                r.get("received_date") or r.get("receivedDate"),
                extra, r.get("created_by", "import"), new_bank_id,
            ),
        )
        if old_id is not None:
            id_map[old_id] = cur.lastrowid
        r["_new_id"] = cur.lastrowid
        r["_new_bank_id"] = new_bank_id

    for r in entry_rows:
        old_link = r.get("linked_sale_id") or r.get("linkedSaleId")
        if old_link is not None and old_link in id_map:
            conn.execute(
                "UPDATE entries SET linked_sale_id=? WHERE id=?",
                (id_map[old_link], r["_new_id"]),
            )

    if payment_rows is None:
        for r in entry_rows:
            if r.get("kind") == "sale" and r.get("received"):
                old_bank_id = r.get("bank_account_id") or r.get("bankAccountId")
                new_bank_id = map_account(old_bank_id) if r.get("method") == "bank" else None
                conn.execute(
                    """INSERT INTO payments(entry_id, date, method, amount, created_by, bank_account_id)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        r["_new_id"],
                        r.get("received_date") or r.get("receivedDate") or r.get("date"),
                        r.get("method") or "cash",
                        float(r.get("amount", 0)),
                        r.get("created_by", "import"), new_bank_id,
                    ),
                )
    else:
        for p in payment_rows:
            old_entry_id = p.get("entry_id")
            new_entry_id = id_map.get(old_entry_id, old_entry_id)
            old_bank_id = p.get("bank_account_id") or p.get("bankAccountId")
            new_bank_id = map_account(old_bank_id) if p.get("method") == "bank" else None
            conn.execute(
                """INSERT INTO payments(entry_id, date, method, amount, loading_unloading, created_by, bank_account_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    new_entry_id, p.get("date"), p.get("method"),
                    float(p.get("amount", 0) or 0), float(p.get("loading_unloading", 0) or 0),
                    p.get("created_by", "import"), new_bank_id,
                ),
            )

    for a in advance_rows:
        try:
            amount = float(a.get("amount", 0))
        except (TypeError, ValueError):
            continue
        old_bank_id = a.get("bank_account_id") or a.get("bankAccountId")
        new_bank_id = map_account(old_bank_id) if a.get("method") == "bank" else None
        conn.execute(
            "INSERT INTO advances(date, client, amount, method, note, created_by, bank_account_id) VALUES (?,?,?,?,?,?,?)",
            (a.get("date"), a.get("client", ""), amount, a.get("method", "cash"), a.get("note", ""), a.get("created_by", "import"), new_bank_id),
        )

    for adj in adj_rows:
        try:
            amount = float(adj.get("amount", 0))
        except (TypeError, ValueError):
            continue
        conn.execute(
            "INSERT INTO client_adjustments(date, client, adj_type, amount, note, created_by) VALUES (?,?,?,?,?,?)",
            (
                adj.get("date"), adj.get("client", ""),
                adj.get("adj_type") or adj.get("adjType", "Adjustment"),
                amount, adj.get("note", ""), adj.get("created_by", "import"),
            ),
        )

    for b in bank_rows:
        try:
            amount = float(b.get("amount", 0))
        except (TypeError, ValueError):
            continue
        if b.get("type") not in BANK_TXN_TYPES:
            continue
        old_bank_id = b.get("bank_account_id") or b.get("bankAccountId")
        new_bank_id = map_account(old_bank_id)
        conn.execute(
            "INSERT INTO bank_transactions(date, type, amount, category, note, created_by, bank_account_id) VALUES (?,?,?,?,?,?,?)",
            (b.get("date"), b.get("type"), amount, b.get("category", ""), b.get("note", ""), b.get("created_by", "import"), new_bank_id),
        )

    if settings_data:
        for key in ("opening_bank_balance", "opening_bank_date"):
            if key in settings_data and settings_data[key] is not None:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(settings_data[key])),
                )

    conn.commit()
    counts = {
        "entries": len(entry_rows),
        "payments": conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"],
        "advances": len(advance_rows),
        "clientAdjustments": len(adj_rows),
        "bankTransactions": len(bank_rows),
        "bankAccounts": conn.execute("SELECT COUNT(*) c FROM bank_accounts").fetchone()["c"],
    }
    conn.close()
    return jsonify({"ok": True, "counts": counts})


@app.route("/api/clear-all", methods=["POST"])
@login_required
@admin_required
def clear_all():
    d = request.get_json(force=True) or {}
    if d.get("confirm") != "DELETE":
        return jsonify({"error": "confirmation text did not match"}), 400
    conn = get_db()
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM advances")
    conn.execute("DELETE FROM client_adjustments")
    conn.execute("DELETE FROM bank_transactions")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)