"""Task Manager App - Flask with SQLite/PostgreSQL storage and per-user tasks."""
import csv
import io
import os
import secrets
import sqlite3
from datetime import date, timedelta
from hmac import compare_digest
from urllib.parse import urlsplit

from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:  # read settings (database password, secret key) from the .env file next to app.py
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass
PRIORITIES = ("low", "medium", "high")
CATEGORIES = ("Academics", "Projects", "Security Lab", "Business", "Personal")
STATUSES = ("todo", "doing", "done")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


class Database:
    """Small adapter for the matching DB-API operations used by this app."""

    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres
        self.integrity_error = sqlite3.IntegrityError
        if postgres:
            import psycopg

            self.integrity_error = psycopg.IntegrityError

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)

    def executemany(self, sql, params):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.executemany(sql, params)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def postgres_connect_args():
    """How to reach PostgreSQL: a full DATABASE_URL, or separate DB_* settings.

    With DB_* settings only DB_PASSWORD is required; the rest default to a local server and the
    task_manager database. A plain password also avoids URL-encoding special characters."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return {"conninfo": url.replace("postgres://", "postgresql://", 1)}
    password = os.environ.get("DB_PASSWORD", "")
    if password:
        return {
            "host": os.environ.get("DB_HOST", "localhost"),
            "port": int(os.environ.get("DB_PORT", "5432")),
            "dbname": os.environ.get("DB_NAME", "task_manager"),
            "user": os.environ.get("DB_USER", "postgres"),
            "password": password,
        }
    return None


def connect_db():
    args = postgres_connect_args()
    if args:
        import psycopg
        from psycopg.rows import dict_row

        return Database(psycopg.connect(**args, row_factory=dict_row), postgres=True)

    if os.environ.get("TASK_MANAGER_USE_SQLITE") != "1":
        raise RuntimeError(
            "DATABASE_URL is not set and no DB_PASSWORD was found. Put your PostgreSQL settings "
            "in a .env file next to app.py (see .env.example). "
            "For intentional SQLite use, set TASK_MANAGER_USE_SQLITE=1."
        )

    db_path = os.environ.get("TASKS_DB", os.path.join(BASE_DIR, "tasks.db"))
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return Database(connection)


def log_database_backend():
    args = postgres_connect_args()
    if args:
        if "conninfo" in args:
            parsed = urlsplit(args["conninfo"])
            name, host = parsed.path.lstrip("/") or "(unspecified)", parsed.hostname or "(unspecified)"
        else:
            name, host = args["dbname"], args["host"]
        app.logger.warning("Database backend: PostgreSQL (database=%s, host=%s)", name, host)
        return

    if os.environ.get("TASK_MANAGER_USE_SQLITE") == "1":
        db_path = os.environ.get("TASKS_DB", os.path.join(BASE_DIR, "tasks.db"))
        app.logger.warning("Database backend: SQLite (path=%s)", db_path)
        return

    raise RuntimeError("No database backend is configured.")


def start_database():
    """Create the tables at startup and turn common PostgreSQL setup problems into plain messages."""
    try:
        init_db()
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith("psycopg"):
            raise SystemExit("The PostgreSQL driver is not installed. Run: pip install -r requirements.txt") from None
        raise
    except Exception as exc:
        if type(exc).__module__.startswith("psycopg"):
            raise SystemExit(
                f"Could not use PostgreSQL: {exc}\n\n"
                "Check that: (1) the PostgreSQL service is running, (2) the database exists "
                "(CREATE DATABASE task_manager;), (3) DB_PASSWORD in your .env file is your "
                "postgres password."
            ) from None
        raise


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = connect_db()
    try:
        if db.postgres:
            db.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    due_date TEXT,
                    category TEXT NOT NULL DEFAULT 'Personal',
                    status TEXT NOT NULL DEFAULT 'todo',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
                )"""
            )
            db.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users(id)")
            cols = {r["column_name"] for r in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'"
            ).fetchall()}
        else:
            db.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id),
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    due_date TEXT,
                    category TEXT NOT NULL DEFAULT 'Personal',
                    status TEXT NOT NULL DEFAULT 'todo',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            cols = {r["name"] for r in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if "user_id" not in cols:
                db.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id)")

        # Migrate databases made before categories/status were added.
        if "category" not in cols:
            db.execute("ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT 'Personal'")
        if "status" not in cols:
            db.execute("ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'todo'")
            if "done" in cols:
                db.execute("UPDATE tasks SET status = 'done' WHERE done = 1")
        db.commit()
    finally:
        db.close()


def row_to_dict(row):
    result = dict(row)
    result.pop("done", None)
    result["done"] = result["status"] == "done"
    return result


def current_user_id():
    return g.user["id"]


@app.before_request
def authenticate_and_protect():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)

    public_endpoints = {"login", "register", "static"}
    if request.endpoint in public_endpoints:
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = get_db().execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
            if g.user:
                if request.endpoint in {"login", "register"}:
                    return redirect(url_for("index"))
                return None
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            return validate_csrf()
        return None

    user_id = session.get("user_id")
    if user_id is None:
        if request.path.startswith("/api/"):
            return jsonify(error="Authentication required."), 401
        return redirect(url_for("login"))

    g.user = get_db().execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not g.user:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify(error="Authentication required."), 401
        return redirect(url_for("login"))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return validate_csrf()
    return None


def validate_csrf():
    submitted_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not submitted_token or not compare_digest(submitted_token, session.get("_csrf_token", "")):
        if request.path.startswith("/api/"):
            return jsonify(error="Invalid or missing CSRF token."), 400
        return render_template("auth_error.html", message="Your session expired. Please try again."), 400
    return None


@app.context_processor
def inject_template_context():
    return {"csrf_token": session.get("_csrf_token", "")}


# ---------- authentication ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif len(password) > 128:
            error = "Password must be 128 characters or fewer."
        else:
            db = get_db()
            try:
                user = db.execute(
                    "INSERT INTO users (email, password_hash) VALUES (?, ?) RETURNING id, email",
                    (email, generate_password_hash(password)),
                ).fetchone()
                db.commit()
                session.clear()
                session["user_id"] = user["id"]
                session["_csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("index"))
            except db.integrity_error:
                db.rollback()
                error = "An account with that email already exists."
    return render_template("register.html", error=error, email=email), (409 if error and "already exists" in error else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            error = "Email or password is incorrect."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["_csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))
    return render_template("login.html", error=error, email=email), (401 if error else 200)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- validation ----------
def clean_payload(data, partial=False):
    """Return (clean_dict, error_message)."""
    out = {}
    if "title" in data or not partial:
        title = str(data.get("title", "")).strip()
        if not title:
            return None, "Title is required."
        if len(title) > 200:
            return None, "Title must be 200 characters or fewer."
        out["title"] = title
    if "notes" in data:
        out["notes"] = str(data["notes"]).strip()[:1000]
    for key, allowed in (("priority", PRIORITIES), ("category", CATEGORIES), ("status", STATUSES)):
        if key in data:
            if data[key] not in allowed:
                return None, f"{key.capitalize()} must be one of: {', '.join(allowed)}."
            out[key] = data[key]
    if "due_date" in data:
        due = data["due_date"] or None
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                return None, "Due date must be YYYY-MM-DD."
        out["due_date"] = due
    return out, None


# ---------- task routes ----------
@app.get("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


@app.get("/api/tasks")
def list_tasks():
    status = request.args.get("status", "all")
    category = request.args.get("category", "all")
    q = request.args.get("q", "").strip()
    sql, params = "SELECT * FROM tasks WHERE user_id = ?", [current_user_id()]
    like = "ILIKE" if get_db().postgres else "LIKE"  # PostgreSQL LIKE is case-sensitive
    if status in STATUSES:
        sql += " AND status = ?"; params.append(status)
    if category in CATEGORIES:
        sql += " AND category = ?"; params.append(category)
    if q:
        sql += f" AND (title {like} ? OR notes {like} ?)"; params += [f"%{q}%", f"%{q}%"]
    sql += (" ORDER BY status = 'done', due_date IS NULL, due_date ASC,"
            " CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC")
    return jsonify([row_to_dict(r) for r in get_db().execute(sql, params).fetchall()])


@app.post("/api/tasks")
def create_task():
    data, err = clean_payload(request.get_json(silent=True) or {})
    if err:
        return jsonify(error=err), 400
    db = get_db()
    row = db.execute(
        """INSERT INTO tasks (user_id, title, notes, priority, due_date, category, status)
           VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *""",
        (current_user_id(), data["title"], data.get("notes", ""), data.get("priority", "medium"),
         data.get("due_date"), data.get("category", "Personal"), data.get("status", "todo")),
    ).fetchone()
    db.commit()
    return jsonify(row_to_dict(row)), 201


@app.put("/api/tasks/<int:task_id>")
def update_task(task_id):
    data, err = clean_payload(request.get_json(silent=True) or {}, partial=True)
    if err:
        return jsonify(error=err), 400
    if not data:
        return jsonify(error="Nothing to update."), 400
    db = get_db()
    sets = ", ".join(f"{key} = ?" for key in data)
    cur = db.execute(
        f"UPDATE tasks SET {sets} WHERE id = ? AND user_id = ?",
        [*data.values(), task_id, current_user_id()],
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Task not found."), 404
    row = db.execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
    ).fetchone()
    return jsonify(row_to_dict(row))


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    db = get_db()
    cur = db.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
    )
    db.commit()
    return ("", 204) if cur.rowcount else (jsonify(error="Task not found."), 404)


@app.get("/api/stats")
def stats():
    rows = get_db().execute(
        "SELECT status, category, due_date FROM tasks WHERE user_id = ?", (current_user_id(),)
    ).fetchall()
    today = date.today()
    out = {"total": len(rows), "todo": 0, "doing": 0, "done": 0, "overdue": 0, "due_soon": 0,
           "by_category": {c: 0 for c in CATEGORIES}}
    for row in rows:
        out[row["status"]] += 1
        out["by_category"][row["category"]] = out["by_category"].get(row["category"], 0) + 1
        if row["status"] != "done" and row["due_date"]:
            due = date.fromisoformat(row["due_date"])
            if due < today:
                out["overdue"] += 1
            elif due <= today + timedelta(days=3):
                out["due_soon"] += 1
    out["completion"] = round(100 * out["done"] / out["total"]) if out["total"] else 0
    return jsonify(out)


@app.get("/api/export.csv")
def export_csv():
    rows = get_db().execute(
        """SELECT title, category, status, priority, due_date, notes, created_at
           FROM tasks WHERE user_id = ? ORDER BY id""",
        (current_user_id(),),
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "category", "status", "priority", "due_date", "notes", "created_at"])
    for row in rows:
        values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        writer.writerow(["'" + value if isinstance(value, str) and value[:1] in "=+-@" else value
                         for value in values])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tasks.csv"})


@app.post("/api/seed")
def seed():
    """Load sample tasks for the signed-in user, only when they have no tasks."""
    db = get_db()
    count_row = db.execute(
        "SELECT COUNT(*) AS task_count FROM tasks WHERE user_id = ?", (current_user_id(),)
    ).fetchone()
    if count_row["task_count"]:
        return jsonify(error="You already have tasks."), 409
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    samples = [
        ("Submit capstone project proposal", "Academics", "high", d(3), "doing"),
        ("Revise networking notes for the CAT", "Academics", "medium", d(6), "todo"),
        ("Build portfolio Projects page", "Projects", "high", d(2), "doing"),
        ("Deploy the Task Manager App", "Projects", "medium", d(9), "todo"),
        ("Set up GitHub profile README", "Projects", "low", d(-2), "done"),
        ("Complete a SQL injection lab", "Security Lab", "medium", d(5), "todo"),
        ("Write up Wi-Fi security audit findings", "Security Lab", "low", d(10), "todo"),
        ("Send quotation to a client", "Business", "high", d(-1), "todo"),
        ("Plan next week's social media posts", "Business", "low", d(4), "todo"),
        ("Gym session and meal prep", "Personal", "low", d(1), "todo"),
    ]
    db.executemany(
        """INSERT INTO tasks (user_id, title, category, priority, due_date, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(current_user_id(), title, category, priority, due, status)
         for title, category, priority, due, status in samples],
    )
    db.commit()
    return jsonify(added=len(samples)), 201


start_database()
log_database_backend()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
