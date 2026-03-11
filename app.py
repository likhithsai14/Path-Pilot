from flask import Flask, render_template, request, redirect, session, url_for, flash
import sqlite3
import os
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecretkey"

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "experience_pdfs")
ALLOWED_FILE_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def is_allowed_pdf(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_FILE_EXTENSIONS


def get_verified_ids(cur):
    cur.execute("SELECT experience_id FROM experience_verifications")
    return {row[0] for row in cur.fetchall()}


def get_admin_notifications(cur, role, limit=10):
    cur.execute(
        """
        SELECT id, message, target_role, created_by, created_at
        FROM admin_notifications
        WHERE target_role IN ('all', ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (role, limit)
    )
    return cur.fetchall()


def get_admin_notification_count(cur, role):
    cur.execute(
        """
        SELECT COUNT(*)
        FROM admin_notifications
        WHERE target_role IN ('all', ?)
        """,
        (role,)
    )
    return cur.fetchone()[0]


def get_admin_unread_notification_count(cur, username, role):
    cur.execute(
        """
        SELECT COALESCE(last_seen_admin_notification_id, 0)
        FROM user_notification_state
        WHERE username=?
        """,
        (username,)
    )
    row = cur.fetchone()
    last_seen_id = row[0] if row else 0

    cur.execute(
        """
        SELECT COUNT(*)
        FROM admin_notifications
        WHERE target_role IN ('all', ?)
        AND id > ?
        """,
        (role, last_seen_id)
    )
    return cur.fetchone()[0]


def is_admin_authenticated():
    return session.get("admin_logged_in") is True


def get_doubt_data(cur, limit=20):
    cur.execute(
        """
        SELECT d.id, d.question, d.asked_by, d.created_at,
               COUNT(a.id) AS answer_count
        FROM doubts d
        LEFT JOIN doubt_answers a ON a.doubt_id = d.id
        GROUP BY d.id
        ORDER BY d.id DESC
        LIMIT ?
        """,
        (limit,)
    )
    doubts = cur.fetchall()

    answers_by_doubt = {}
    if doubts:
        doubt_ids = [str(row[0]) for row in doubts]
        placeholders = ",".join(["?"] * len(doubt_ids))
        cur.execute(
            f"""
            SELECT id, doubt_id, answer, answered_by, created_at
            FROM doubt_answers
            WHERE doubt_id IN ({placeholders})
            ORDER BY id ASC
            """,
            doubt_ids
        )
        for ans in cur.fetchall():
            answers_by_doubt.setdefault(ans[1], []).append(ans)

    return doubts, answers_by_doubt


# ---------------- DATABASE ---------------- #

def init_db():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # USERS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        designation TEXT,
        approved INTEGER DEFAULT 1,
        is_banned INTEGER DEFAULT 0
    )
    """)

    # EXPERIENCES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS experiences(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT,
        job_role TEXT,
        year INTEGER,
        description TEXT,
        posted_by TEXT,
        interview_date TEXT,
        outcome TEXT,
        package_offered TEXT,
        views INTEGER DEFAULT 0
    )
    """)

    # COMPANIES
    cur.execute("""
CREATE TABLE IF NOT EXISTS companies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    date TEXT
)
""")

    # BOOKMARKS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookmarks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        experience_id INTEGER,
        UNIQUE(username, experience_id)
    )
    """)

    # EXPERIENCE DOCUMENTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS experience_documents(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experience_id INTEGER,
        file_path TEXT,
        original_name TEXT,
        uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # RECENTLY VIEWED EXPERIENCES (per user)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS experience_views(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        experience_id INTEGER,
        last_viewed TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(username, experience_id)
    )
    """)

    # VERIFIED EXPERIENCES (approved by admin)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS experience_verifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        experience_id INTEGER UNIQUE,
        verified_by TEXT,
        verified_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ADMIN BROADCAST NOTIFICATIONS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        target_role TEXT NOT NULL DEFAULT 'all',
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # USER NOTIFICATION READ STATE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_notification_state(
        username TEXT PRIMARY KEY,
        last_seen_admin_notification_id INTEGER DEFAULT 0
    )
    """)

    # STUDENT DOUBTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS doubts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        asked_by TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # CONTRIBUTOR ANSWERS ON DOUBTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS doubt_answers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doubt_id INTEGER NOT NULL,
        answer TEXT NOT NULL,
        answered_by TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Add columns if they don't exist (for migration)
    for col_def in [
        "interview_date TEXT",
        "outcome TEXT",
        "package_offered TEXT",
        "views INTEGER DEFAULT 0"
    ]:
        try:
            cur.execute(f"ALTER TABLE experiences ADD COLUMN {col_def}")
        except:
            pass

    # Add user profile columns if they don't exist
    for col_def in [
        "full_name TEXT",
        "college TEXT",
        "class_name TEXT"
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
        except:
            pass

    # Add user ban flag if it doesn't exist
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except:
        pass

    conn.commit()
    conn.close()

init_db()


# Test database health
@app.route("/test_db")
def test_db():
    try:
        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(experiences)")
        columns = cur.fetchall()
        conn.close()
        return {
            "status": "Database OK",
            "columns": [f"{col[1]} ({col[2]})" for col in columns]
        }
    except Exception as e:
        return {"status": "Error", "message": str(e)}


# ---------------- HOME ---------------- #

@app.route("/")
def home():
    # Handle admin sessions (admin login does not set session["role"])
    if is_admin_authenticated() and "role" not in session:
        admin_username = session.get("admin_user", "Admin")
        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT e.*, u.role, u.designation
            FROM experience_views ev
            JOIN experiences e ON ev.experience_id = e.id
            LEFT JOIN users u ON e.posted_by = u.username
            WHERE ev.username=?
            ORDER BY ev.last_viewed DESC
            LIMIT 30
        """, (admin_username,))
        experiences = cur.fetchall()
        verified_ids = get_verified_ids(cur)
        cur.execute("""
            SELECT id, username, designation
            FROM users
            WHERE role='contributor' AND approved=0
        """)
        pending_users = cur.fetchall()
        doubts, doubt_answers = get_doubt_data(cur, limit=20)
        conn.close()
        return render_template(
            "dashboard.html",
            user=admin_username,
            display_name="Admin",
            role="admin",
            experiences=experiences,
            verified_ids=verified_ids,
            bookmarked_ids=set(),
            pending_users=pending_users,
            admin_notifications=[],
            notification_count=0,
            doubts=doubts,
            doubt_answers=doubt_answers,
            page="home"
        )

    if "role" not in session:
        return render_template("home.html", is_logged_in=False)

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # Load only recently viewed experiences for in-app Home feed
    cur.execute("""
        SELECT e.*, u.role, u.designation
        FROM experience_views ev
        JOIN experiences e ON ev.experience_id = e.id
        LEFT JOIN users u ON e.posted_by = u.username
        WHERE ev.username=?
        ORDER BY ev.last_viewed DESC
        LIMIT 30
    """, (session["user"],))
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    pending_users = []
    bookmarked_ids = set()
    admin_notifications = []
    notification_count = 0
    doubts = []
    doubt_answers = {}

    if session["role"] != "admin":
        cur.execute(
            "SELECT experience_id FROM bookmarks WHERE username=?",
            (session["user"],)
        )
        bookmarked_ids = {row[0] for row in cur.fetchall()}

    if session["role"] == "admin":
        cur.execute("""
        SELECT id, username, designation
        FROM users
        WHERE role='contributor' AND approved=0
        """)
        pending_users = cur.fetchall()
    else:
        admin_notifications = get_admin_notifications(cur, session["role"], limit=12)
        notification_count = get_admin_unread_notification_count(cur, session["user"], session["role"])

    if session["role"] in ["student", "contributor", "admin"]:
        doubts, doubt_answers = get_doubt_data(cur, limit=20)

    conn.close()

    return render_template(
        "dashboard.html",
        user=session["user"],
        display_name=session.get("display_name", session["user"].split("@")[0].capitalize()),
        role=session["role"],
        experiences=experiences,
        verified_ids=verified_ids,
        bookmarked_ids=bookmarked_ids,
        pending_users=pending_users,
        admin_notifications=admin_notifications,
        notification_count=notification_count,
        doubts=doubts,
        doubt_answers=doubt_answers,
        page="home"
    )


# ---------------- REGISTER ---------------- #

@app.route("/register/<role>", methods=["GET", "POST"])
def register(role):

    if role not in ["student", "contributor"]:
        return redirect("/")

    if request.method == "POST":

        username = request.form.get("username").lower().strip()
        password = request.form.get("password")

        designation = None
        approved = 1

        if role == "contributor":
            designation = request.form.get("designation")
            approved = 0

        hashed_password = generate_password_hash(password)

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO users(username,password,role,designation,approved)
                VALUES(?,?,?,?,?)
            """, (username, hashed_password, role, designation, approved))

            conn.commit()
            conn.close()

            if role == "contributor":
                return render_template(
                    "auth_notice.html",
                    title="Registration Submitted",
                    message="Registration successful. Wait for admin approval before logging in.",
                    cta_text="Back to Home",
                    cta_link="/"
                )

            return redirect(url_for("login", role=role))

        except:
            conn.close()
            return render_template(
                "auth_notice.html",
                title="Account Already Exists",
                message="A user with this email already exists. Please log in with your existing account.",
                cta_text="Go to Login",
                cta_link=url_for("login", role=role)
            )

    return render_template("register.html", role=role)


# ---------------- LOGIN ---------------- #

@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):

    if role not in ["student", "contributor", "admin"]:
        return redirect("/")

    if request.method == "GET":
        return render_template("login.html", role=role)

    if request.method == "POST":

        username = request.form.get("username").lower().strip()
        password = request.form.get("password")

        # ADMIN LOGIN
        if role == "admin":
            if username == "admin@cmrcet.ac.in" and password == "admin123":
                session["admin_logged_in"] = True
                session["admin_user"] = "Admin"
                return redirect("/admin/home")
            else:
                return "Invalid Admin Credentials!"

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id, username, password, role, designation, approved, full_name, college, class_name, is_banned
            FROM users
            WHERE username=? AND role=?
            """,
            (username, role)
        )
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):

            if user[9] == 1:
                return "Your account has been banned by admin."

            if role == "contributor" and user[5] == 0:
                return "Your account is waiting for admin approval."

            # Preserve separate admin session while updating normal user session.
            session.pop("user", None)
            session.pop("role", None)
            session.pop("designation", None)
            session.pop("display_name", None)
            session["user"] = username
            session["role"] = role
            session["designation"] = user[4]
            session["display_name"] = user[6] if user[6] else username.split("@")[0].capitalize()

            return redirect("/")

        return "Invalid credentials!"

    return render_template("login.html", role=role)


# ---------------- DASHBOARD ---------------- #

@app.route("/dashboard")
def dashboard():

    if "role" not in session:
        if is_admin_authenticated():
            return redirect("/admin/home")
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    pending_users = []
    bookmarked_ids = set()
    admin_notifications = []
    notification_count = 0
    doubts = []
    doubt_answers = {}

    # total reviews
    cur.execute("SELECT COUNT(*) FROM experiences")
    total_reviews = cur.fetchone()[0]

    # total companies
    cur.execute("SELECT COUNT(*) FROM companies")
    total_companies = cur.fetchone()[0]

    # total bookmarks for student
    if session["role"] != "admin":
        cur.execute(
            "SELECT experience_id FROM bookmarks WHERE username=?",
            (session["user"],)
        )
        bookmarked_ids = {row[0] for row in cur.fetchall()}
    total_bookmarks = len(bookmarked_ids)

    # total contributors (for admin dashboard)
    cur.execute("SELECT COUNT(*) FROM users WHERE role='contributor'")
    total_contributors = cur.fetchone()[0]

    # load experiences
    cur.execute("""
        SELECT e.*, u.role, u.designation
        FROM experiences e
        LEFT JOIN users u ON e.posted_by = u.username
        ORDER BY e.id DESC
    """)
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    # Admin pending approvals for notification bell
    if session["role"] == "admin":
        cur.execute("""
        SELECT id, username, designation
        FROM users
        WHERE role='contributor' AND approved=0
        """)
        pending_users = cur.fetchall()
    else:
        admin_notifications = get_admin_notifications(cur, session["role"], limit=12)
        notification_count = get_admin_unread_notification_count(cur, session["user"], session["role"])

    if session["role"] in ["student", "contributor", "admin"]:
        doubts, doubt_answers = get_doubt_data(cur, limit=20)

    conn.close()

    return render_template(
        "dashboard.html",
        user=session["user"],
        display_name=session.get("display_name", session["user"].split("@")[0].capitalize()),
        role=session["role"],
        experiences=experiences,
        verified_ids=verified_ids,
        bookmarked_ids=bookmarked_ids,
        pending_users=pending_users,
        total_reviews=total_reviews,
        total_bookmarks=total_bookmarks,
        total_contributors=total_contributors,
        total_companies=total_companies,
        admin_notifications=admin_notifications,
        notification_count=notification_count,
        doubts=doubts,
        doubt_answers=doubt_answers,
        page="dashboard"
    )


@app.route("/admin/home")
def admin_home():

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM experiences")
    total_reviews = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM companies")
    total_companies = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE role='contributor'")
    total_contributors = cur.fetchone()[0]

    cur.execute(
        """
        SELECT e.*, u.role, u.designation
        FROM experiences e
        LEFT JOIN users u ON e.posted_by = u.username
        ORDER BY e.id DESC
        """
    )
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    cur.execute(
        """
        SELECT id, username, designation
        FROM users
        WHERE role='contributor' AND approved=0
        """
    )
    pending_users = cur.fetchall()

    doubts, doubt_answers = get_doubt_data(cur, limit=20)

    conn.close()

    return render_template(
        "dashboard.html",
        user=session.get("admin_user", "Admin"),
        display_name="Admin",
        role="admin",
        experiences=experiences,
        verified_ids=verified_ids,
        bookmarked_ids=set(),
        pending_users=pending_users,
        total_reviews=total_reviews,
        total_bookmarks=0,
        total_contributors=total_contributors,
        total_companies=total_companies,
        admin_notifications=[],
        notification_count=0,
        doubts=doubts,
        doubt_answers=doubt_answers,
        page="dashboard"
    )


@app.route("/doubts/ask", methods=["POST"])
def ask_doubt():

    if "role" not in session or session["role"] != "student":
        return redirect("/dashboard")

    question = request.form.get("question", "").strip()
    if not question:
        flash("Please enter your doubt before submitting.", "error")
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO doubts(question, asked_by)
        VALUES(?, ?)
        """,
        (question, session["user"])
    )
    conn.commit()
    conn.close()

    flash("Doubt posted. Contributors can answer it now.", "success")
    return redirect("/dashboard")


@app.route("/doubts/edit/<int:doubt_id>", methods=["POST"])
def edit_doubt(doubt_id):

    if "role" not in session or session["role"] != "student":
        return redirect("/dashboard")

    question = request.form.get("question", "").strip()
    if not question:
        flash("Doubt cannot be empty.", "error")
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE doubts
        SET question=?
        WHERE id=? AND asked_by=?
        """,
        (question, doubt_id, session["user"])
    )

    if cur.rowcount == 0:
        conn.close()
        flash("You can edit only your own doubts.", "error")
        return redirect("/dashboard")

    conn.commit()
    conn.close()
    flash("Doubt updated successfully.", "success")
    return redirect("/dashboard")


@app.route("/doubts/delete/<int:doubt_id>", methods=["POST"])
def delete_doubt(doubt_id):

    if "role" not in session or session["role"] != "student":
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM doubts WHERE id=? AND asked_by=?",
        (doubt_id, session["user"])
    )

    if cur.rowcount == 0:
        conn.close()
        flash("You can delete only your own doubts.", "error")
        return redirect("/dashboard")

    cur.execute("DELETE FROM doubt_answers WHERE doubt_id=?", (doubt_id,))
    conn.commit()
    conn.close()

    flash("Doubt deleted successfully.", "success")
    return redirect("/dashboard")


@app.route("/doubts/answer/<int:doubt_id>", methods=["POST"])
def answer_doubt(doubt_id):

    if not (session.get("role") == "contributor" or is_admin_authenticated()):
        return redirect("/dashboard")

    answer = request.form.get("answer", "").strip()
    if not answer:
        flash("Please type an answer before submitting.", "error")
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT id FROM doubts WHERE id=?", (doubt_id,))
    doubt = cur.fetchone()
    if not doubt:
        conn.close()
        flash("This doubt no longer exists.", "error")
        return redirect("/dashboard")

    answered_by = session.get("user", session.get("admin_user", "Admin"))
    cur.execute(
        """
        INSERT INTO doubt_answers(doubt_id, answer, answered_by)
        VALUES(?, ?, ?)
        """,
        (doubt_id, answer, answered_by)
    )
    conn.commit()
    conn.close()

    flash("Answer posted successfully.", "success")
    return redirect("/dashboard")


@app.route("/doubts/answer/edit/<int:answer_id>", methods=["POST"])
def edit_doubt_answer(answer_id):

    if not (session.get("role") == "contributor" or is_admin_authenticated()):
        return redirect("/dashboard")

    answer = request.form.get("answer", "").strip()
    if not answer:
        flash("Answer cannot be empty.", "error")
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    answered_by = session.get("user", session.get("admin_user", "Admin"))
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE doubt_answers
        SET answer=?
        WHERE id=? AND answered_by=?
        """,
        (answer, answer_id, answered_by)
    )

    if cur.rowcount == 0:
        conn.close()
        flash("You can edit only your own answers.", "error")
        return redirect("/dashboard")

    conn.commit()
    conn.close()
    flash("Answer updated successfully.", "success")
    return redirect("/dashboard")


@app.route("/doubts/answer/delete/<int:answer_id>", methods=["POST"])
def delete_doubt_answer(answer_id):

    if not (session.get("role") == "contributor" or is_admin_authenticated()):
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    answered_by = session.get("user", session.get("admin_user", "Admin"))
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM doubt_answers WHERE id=? AND answered_by=?",
        (answer_id, answered_by)
    )

    if cur.rowcount == 0:
        conn.close()
        flash("You can delete only your own answers.", "error")
        return redirect("/dashboard")

    conn.commit()
    conn.close()
    flash("Answer deleted successfully.", "success")
    return redirect("/dashboard")


# ---------------- PROFILE SETTINGS ---------------- #

@app.route("/profile", methods=["GET", "POST"])
def profile_settings():

    if "role" not in session:
        return redirect("/")

    if session["role"] not in ["student", "contributor"]:
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        college = request.form.get("college", "").strip()
        class_name = request.form.get("class_name", "").strip()

        if not full_name or not college:
            flash("Name and college are required.", "error")
        else:
            cur.execute(
                """
                UPDATE users
                SET full_name=?, college=?, class_name=?
                WHERE username=?
                """,
                (full_name, college, class_name if class_name else None, session["user"])
            )
            conn.commit()
            session["display_name"] = full_name
            flash("Profile updated successfully.", "success")

    cur.execute(
        "SELECT full_name, college, class_name FROM users WHERE username=?",
        (session["user"],)
    )
    profile = cur.fetchone()
    conn.close()

    return render_template(
        "profile.html",
        role=session["role"],
        user=session["user"],
        full_name=profile[0] if profile else "",
        college=profile[1] if profile else "",
        class_name=profile[2] if profile and profile[2] else ""
    )
# ---------------- SEARCH ---------------- #

@app.route("/search", methods=["GET", "POST"])
def search():

    if "role" not in session:
        return redirect("/")

    results = []
    verified_ids = set()

    if request.method == "POST":

        keyword = request.form.get("keyword")

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        cur.execute("""
            SELECT e.*, u.role, u.designation FROM experiences e
            LEFT JOIN users u ON e.posted_by = u.username
            WHERE e.company LIKE ? OR e.description LIKE ?
        """, (f"%{keyword}%", f"%{keyword}%"))

        results = cur.fetchall()
        verified_ids = get_verified_ids(cur)
        conn.close()

    return render_template(
        "search.html",
        results=results,
        verified_ids=verified_ids,
        user=session["user"]
    )


# ---------------- COMPANIES ---------------- #

@app.route("/companies")
def companies():

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT name, date FROM companies")
    companies = cur.fetchall()

    cur.execute("""
        SELECT e.*, u.role, u.designation
        FROM experiences e
        LEFT JOIN users u ON e.posted_by = u.username
        ORDER BY e.id DESC
    """)
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    conn.close()

    return render_template(
        "companies.html",
        companies=companies,
        experiences=experiences,
        verified_ids=verified_ids,
        role=session["role"],
        user=session["user"]
    )


@app.route("/experience/view/<int:exp_id>", methods=["POST"])
def track_experience_view(exp_id):

    if "role" not in session:
        return {"status": "unauthorized"}, 401

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("UPDATE experiences SET views = COALESCE(views, 0) + 1 WHERE id=?", (exp_id,))
    # SQLite-compatible upsert (works across older versions too)
    cur.execute(
        """
        UPDATE experience_views
        SET last_viewed=CURRENT_TIMESTAMP
        WHERE username=? AND experience_id=?
        """,
        (session["user"], exp_id)
    )

    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO experience_views(username,experience_id,last_viewed)
            VALUES(?,?,CURRENT_TIMESTAMP)
            """,
            (session["user"], exp_id)
        )

    conn.commit()
    conn.close()

    return {"status": "ok"}

@app.route("/company/<name>")
def company_page(name):

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # perform case-insensitive match using LOWER
    print(f"DEBUG: Received company name: {repr(name)}")
    cur.execute("""
        SELECT e.*, u.role, u.designation FROM experiences e
        LEFT JOIN users u ON e.posted_by = u.username
        WHERE LOWER(e.company)=LOWER(?)
    """, (name,))
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)
    print(f"DEBUG: Found {len(experiences)} experiences for {name}")
    for exp in experiences:
        print(f"DEBUG: Experience company={exp[1]}, role={exp[2]}")

    conn.close()

    return render_template(
        "company_page.html",
        company=name,
        experiences=experiences,
        verified_ids=verified_ids,
        user=session["user"]
    )


# ---------------- ADD COMPANY ---------------- #

@app.route("/admin/add_company", methods=["GET","POST"])
def add_company():

    if not is_admin_authenticated():
        return redirect("/login/admin")

    if request.method == "POST":

        company = request.form.get("company")
        date = request.form.get("date")

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        try:
            cur.execute(
                "INSERT INTO companies(name,date) VALUES(?,?)",
                (company,date)
            )
            conn.commit()
        except:
            pass

        conn.close()

        return redirect("/companies")

    return render_template("add_company.html")

@app.route("/admin/delete_company/<name>")
def delete_company(name):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM companies WHERE name=?", (name,))
    conn.commit()
    conn.close()

    return redirect("/companies")

@app.route("/admin/edit_company/<name>", methods=["GET","POST"])
def edit_company(name):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    if request.method == "POST":

        new_name = request.form.get("company")
        new_date = request.form.get("date")

        cur.execute(
            "UPDATE companies SET name=?, date=? WHERE name=?",
            (new_name, new_date, name)
        )
        conn.commit()
        conn.close()

        return redirect("/companies")

    cur.execute("SELECT * FROM companies WHERE name=?", (name,))
    company = cur.fetchone()
    conn.close()

    return render_template("edit_company.html", company=company)


@app.route("/admin/controls")
def admin_controls():

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM experiences ORDER BY id DESC")
    experiences = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    cur.execute(
        """
        SELECT id, username, full_name, college, class_name, is_banned
        FROM users
        WHERE role='student'
        ORDER BY id DESC
        """
    )
    student_users = cur.fetchall()

    cur.execute(
        """
        SELECT id, username, designation, full_name, college, class_name, approved, is_banned
        FROM users
        WHERE role='contributor'
        ORDER BY id DESC
        """
    )
    contributor_users = cur.fetchall()

    cur.execute(
        """
        SELECT id, message, target_role, created_by, created_at
        FROM admin_notifications
        ORDER BY id DESC
        LIMIT 10
        """
    )
    recent_notifications = cur.fetchall()

    conn.close()

    return render_template(
        "admin_manage.html",
        experiences=experiences,
        student_users=student_users,
        contributor_users=contributor_users,
        verified_ids=verified_ids,
        recent_notifications=recent_notifications
    )


@app.route("/admin/send_notification", methods=["POST"])
def admin_send_notification():

    if not is_admin_authenticated():
        return redirect("/login/admin")

    message = request.form.get("message", "").strip()
    target_role = request.form.get("target_role", "all").strip().lower()

    if not message:
        flash("Message cannot be empty.", "error")
        return redirect("/admin/controls")

    if target_role not in ["all", "student", "contributor"]:
        target_role = "all"

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO admin_notifications(message, target_role, created_by)
        VALUES(?,?,?)
        """,
        (message, target_role, session.get("admin_user", "Admin"))
    )

    conn.commit()
    conn.close()

    flash("Notification sent successfully.", "success")
    return redirect("/admin/controls")


@app.route("/notifications/mark_read", methods=["POST"])
def mark_notifications_read():

    if "role" not in session:
        return {"status": "unauthorized"}, 401

    if session["role"] == "admin":
        return {"status": "skipped"}

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COALESCE(MAX(id), 0)
        FROM admin_notifications
        WHERE target_role IN ('all', ?)
        """,
        (session["role"],)
    )
    latest_visible_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO user_notification_state(username, last_seen_admin_notification_id)
        VALUES(?, ?)
        ON CONFLICT(username)
        DO UPDATE SET last_seen_admin_notification_id=excluded.last_seen_admin_notification_id
        """,
        (session["user"], latest_visible_id)
    )

    conn.commit()
    conn.close()

    return {"status": "ok", "last_seen": latest_visible_id}


@app.route("/admin/delete_experience/<int:exp_id>")
def admin_delete_experience(exp_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM bookmarks WHERE experience_id=?", (exp_id,))
    cur.execute("DELETE FROM experience_views WHERE experience_id=?", (exp_id,))
    cur.execute("DELETE FROM experience_documents WHERE experience_id=?", (exp_id,))
    cur.execute("DELETE FROM experience_verifications WHERE experience_id=?", (exp_id,))
    cur.execute("DELETE FROM experiences WHERE id=?", (exp_id,))

    conn.commit()
    conn.close()

    return redirect("/admin/controls")


@app.route("/admin/verify_experience/<int:exp_id>", methods=["POST"])
def admin_verify_experience(exp_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR IGNORE INTO experience_verifications(experience_id, verified_by)
        VALUES(?,?)
        """,
        (exp_id, session.get("admin_user", "Admin"))
    )

    conn.commit()
    conn.close()

    return redirect("/admin/controls")


@app.route("/admin/ban_user/<int:user_id>", methods=["POST"])
def admin_ban_user(user_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET is_banned=1
        WHERE id=? AND role IN ('student', 'contributor')
        """,
        (user_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/admin/controls")


@app.route("/admin/unban_user/<int:user_id>", methods=["POST"])
def admin_unban_user(user_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET is_banned=0
        WHERE id=? AND role IN ('student', 'contributor')
        """,
        (user_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/admin/controls")

# ---------------- SHARE EXPERIENCE ---------------- #

@app.route("/share", methods=["GET", "POST"])
def share():

    if not (session.get("role") == "contributor" or is_admin_authenticated()):
        return redirect("/dashboard")

    posted_by = session.get("user", session.get("admin_user", "Admin"))

    if request.method == "POST":

        # normalize company name to Title Case for consistency
        raw_company = request.form.get("company")
        company = raw_company.strip().title() if raw_company else None
        job_role = request.form.get("job_role")
        year = request.form.get("year")
        description = request.form.get("description")
        interview_date = request.form.get("interview_date")
        outcome = request.form.get("outcome")
        package_offered = request.form.get("package_offered")
        pdf_file = request.files.get("experience_pdf")

        saved_pdf_path = None
        original_pdf_name = None

        # simple validation
        if not company or not job_role or not year or not description:
            flash("Please fill all required fields", "error")
            return render_template("share.html")

        if pdf_file and pdf_file.filename:
            original_pdf_name = secure_filename(pdf_file.filename)
            if not is_allowed_pdf(original_pdf_name):
                flash("Only PDF files are allowed.", "error")
                return render_template("share.html")

            unique_filename = f"{uuid.uuid4().hex}_{original_pdf_name}"
            file_save_path = os.path.join(UPLOAD_FOLDER, unique_filename)

            try:
                pdf_file.save(file_save_path)
                saved_pdf_path = f"uploads/experience_pdfs/{unique_filename}"
            except Exception:
                flash("Failed to upload PDF. Please try again.", "error")
                return render_template("share.html")

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO experiences(company,job_role,year,description,posted_by,interview_date,outcome,package_offered)
                VALUES(?,?,?,?,?,?,?,?)
            """, (company, job_role, year, description, posted_by, interview_date, outcome, package_offered))

            exp_id = cur.lastrowid

            if saved_pdf_path:
                cur.execute(
                    """
                    INSERT INTO experience_documents(experience_id,file_path,original_name)
                    VALUES(?,?,?)
                    """,
                    (exp_id, saved_pdf_path, original_pdf_name)
                )

            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Error saving experience: {e}", "error")
            conn.close()
            return render_template("share.html")
        conn.close()

        # debug output
        print(f"Experience posted: {company} / {job_role} by {session.get('user')}")

        # After posting, send user to the company experience list so they can see their entry
        flash("Experience posted successfully!", "success")
        return redirect(url_for('company_page', name=company))

    return render_template("share.html")


# ---------------- APPROVE ---------------- #

@app.route("/approve/<int:user_id>")
def approve(user_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/admin/home")


# ---------------- REJECT ---------------- #

@app.route("/reject/<int:user_id>")
def reject(user_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/admin/home")
# ---------------- BOOKMARK PAGE ---------------- #

@app.route("/bookmarks")
def bookmarks():

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("""
    SELECT e.*, u.role, u.designation
    FROM bookmarks b
    JOIN experiences e ON b.experience_id = e.id
    LEFT JOIN users u ON e.posted_by = u.username
    WHERE b.username=?
    ORDER BY e.id DESC
    """, (session["user"],))

    bookmarks = cur.fetchall()
    verified_ids = get_verified_ids(cur)

    conn.close()

    return render_template(
        "bookmarks.html",
        bookmarks=bookmarks,
        verified_ids=verified_ids,
        user=session["user"],
        role=session["role"]
    )


# ---------------- ADD BOOKMARK ---------------- #

@app.route("/bookmark/<int:exp_id>")
def bookmark(exp_id):

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO bookmarks(username,experience_id) VALUES(?,?)",
        (session["user"], exp_id)
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer)


# ---------------- REMOVE BOOKMARK ---------------- #

@app.route("/remove_bookmark/<int:exp_id>")
def remove_bookmark(exp_id):

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM bookmarks WHERE username=? AND experience_id=?",
        (session["user"], exp_id)
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer)
# ---------------- LOGOUT ---------------- #

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)