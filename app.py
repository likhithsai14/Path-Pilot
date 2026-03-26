from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask import Flask, render_template, request, redirect, session, url_for, flash, send_from_directory
import sqlite3
import os
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecretkey"
APP_BOOT_ID = uuid.uuid4().hex

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "experience_pdfs")
CONTACT_UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads", "contact_files")
ALLOWED_FILE_EXTENSIONS = {"pdf"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CONTACT_UPLOAD_FOLDER, exist_ok=True)


def is_allowed_pdf(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_FILE_EXTENSIONS


@app.before_request
def reset_session_after_server_restart():
    # Ensure each server restart begins with a fresh session state.
    if session.get("_app_boot_id") != APP_BOOT_ID:
        session.clear()
        session["_app_boot_id"] = APP_BOOT_ID


def get_verified_ids(cur):
    cur.execute("SELECT experience_id FROM experience_verifications")
    return {row[0] for row in cur.fetchall()}


def get_admin_notifications(cur, role, username=None, limit=10):
    if username:
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
            SELECT id, message, target_role, created_by, created_at
            FROM admin_notifications
            WHERE target_role IN ('all', ?)
            AND id > ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (role, last_seen_id, limit)
        )
        return cur.fetchall()

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


def get_active_viewer_username():
    if "user" in session:
        return session["user"]
    if is_admin_authenticated():
        return session.get("admin_user", "Admin")
    return None


def record_experience_view(cur, viewer_name, exp_id):
    cur.execute("UPDATE experiences SET views = COALESCE(views, 0) + 1 WHERE id=?", (exp_id,))
    cur.execute(
        """
        UPDATE experience_views
        SET last_viewed=CURRENT_TIMESTAMP
        WHERE username=? AND experience_id=?
        """,
        (viewer_name, exp_id)
    )

    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO experience_views(username,experience_id,last_viewed)
            VALUES(?,?,CURRENT_TIMESTAMP)
            """,
            (viewer_name, exp_id)
        )


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


def get_preparation_progress(cur, username):
    cur.execute(
        """
        SELECT dsa_questions, aptitude_sets, mock_interviews,
               core_subject_revisions, company_rounds_reviewed,
               weekly_hours_target, weekly_hours_completed,
               focus_area, updated_at
        FROM preparation_progress
        WHERE username=?
        """,
        (username,)
    )
    row = cur.fetchone()

    progress = {
        "dsa_questions": 0,
        "aptitude_sets": 0,
        "mock_interviews": 0,
        "core_subject_revisions": 0,
        "company_rounds_reviewed": 0,
        "weekly_hours_target": 10,
        "weekly_hours_completed": 0,
        "focus_area": "",
        "updated_at": None,
    }

    if row:
        progress.update({
            "dsa_questions": row[0] or 0,
            "aptitude_sets": row[1] or 0,
            "mock_interviews": row[2] or 0,
            "core_subject_revisions": row[3] or 0,
            "company_rounds_reviewed": row[4] or 0,
            "weekly_hours_target": row[5] or 10,
            "weekly_hours_completed": row[6] or 0,
            "focus_area": row[7] or "",
            "updated_at": row[8],
        })

    metric_specs = [
        ("dsa_questions", "DSA Questions Solved", 120, "questions"),
        ("aptitude_sets", "Aptitude Sets Completed", 20, "sets"),
        ("mock_interviews", "Mock Interviews Taken", 8, "mocks"),
        ("core_subject_revisions", "Core Subject Revisions", 12, "topics"),
        ("company_rounds_reviewed", "Company Experiences Reviewed", 30, "reviews"),
        ("weekly_hours_completed", "Weekly Practice Hours", max(progress["weekly_hours_target"], 1), "hours"),
    ]

    metrics = []
    score_total = 0
    lowest_metric = None

    for key, label, target, unit in metric_specs:
        current = progress[key] or 0
        percent = min(int((current / target) * 100), 100) if target > 0 else 0
        metric = {
            "key": key,
            "label": label,
            "current": current,
            "target": target,
            "unit": unit,
            "percent": percent,
        }
        metrics.append(metric)
        score_total += percent
        if lowest_metric is None or percent < lowest_metric["percent"]:
            lowest_metric = metric

    overall_score = round(score_total / len(metrics)) if metrics else 0

    if overall_score >= 75:
        readiness_label = "Interview Ready"
        readiness_text = "Your preparation is balanced. Keep doing mocks and targeted company revision before applying."
    elif overall_score >= 45:
        readiness_label = "Building Momentum"
        readiness_text = "You have a working base. Push the weakest area for the next 7 days to lift your readiness quickly."
    else:
        readiness_label = "Foundation Stage"
        readiness_text = "Your preparation needs more consistency. Build a repeatable weekly rhythm before increasing difficulty."

    focus_recommendation = "Maintain balanced practice across all tracks."
    if lowest_metric:
        focus_recommendation = f"Next priority: improve {lowest_metric['label'].lower()} toward {lowest_metric['target']} {lowest_metric['unit']}."

    return {
        "values": progress,
        "metrics": metrics,
        "overall_score": overall_score,
        "readiness_label": readiness_label,
        "readiness_text": readiness_text,
        "focus_recommendation": focus_recommendation,
    }


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

    # STUDENT PREPARATION PROGRESS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS preparation_progress(
        username TEXT PRIMARY KEY,
        dsa_questions INTEGER DEFAULT 0,
        aptitude_sets INTEGER DEFAULT 0,
        mock_interviews INTEGER DEFAULT 0,
        core_subject_revisions INTEGER DEFAULT 0,
        company_rounds_reviewed INTEGER DEFAULT 0,
        weekly_hours_target INTEGER DEFAULT 10,
        weekly_hours_completed INTEGER DEFAULT 0,
        focus_area TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # CONTRIBUTOR -> ADMIN CONTACT MESSAGES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contributor_admin_contacts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contributor_username TEXT NOT NULL,
        subject TEXT NOT NULL,
        message TEXT NOT NULL,
        admin_reply TEXT,
        replied_at TEXT,
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

    # Add contributor contact columns if they don't exist
    for col_def in [
        "admin_reply TEXT",
        "replied_at TEXT"
    ]:
        try:
            cur.execute(f"ALTER TABLE contributor_admin_contacts ADD COLUMN {col_def}")
        except:
            pass

        for col_def in [
            "file_path TEXT",
            "original_name TEXT"
        ]:
            try:
                cur.execute(f"ALTER TABLE contributor_admin_contacts ADD COLUMN {col_def}")
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
        admin_notifications = get_admin_notifications(
            cur,
            session["role"],
            username=session["user"],
            limit=12
        )
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
    preparation_progress = None
    contributor_contact_messages = []

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
        admin_notifications = get_admin_notifications(
            cur,
            session["role"],
            username=session["user"],
            limit=12
        )
        notification_count = get_admin_unread_notification_count(cur, session["user"], session["role"])

    if session["role"] in ["student", "contributor", "admin"]:
        doubts, doubt_answers = get_doubt_data(cur, limit=20)

    if session["role"] == "student":
        preparation_progress = get_preparation_progress(cur, session["user"])

    if session["role"] == "contributor":
        cur.execute(
            """
            SELECT id, subject, message, created_at, admin_reply, replied_at, file_path, original_name
            FROM contributor_admin_contacts
            WHERE contributor_username=?
            ORDER BY id DESC
            LIMIT 5
            """,
            (session["user"],)
        )
        contributor_contact_messages = cur.fetchall()

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
        preparation_progress=preparation_progress,
        contributor_contact_messages=contributor_contact_messages,
        page="dashboard"
    )


@app.route("/preparation-progress/update", methods=["POST"])
def update_preparation_progress():

    if session.get("role") != "student":
        return redirect("/dashboard")

    def parse_non_negative(name, default=0):
        raw_value = request.form.get(name, str(default)).strip()
        try:
            return max(int(raw_value), 0)
        except ValueError:
            return default

    dsa_questions = parse_non_negative("dsa_questions")
    aptitude_sets = parse_non_negative("aptitude_sets")
    mock_interviews = parse_non_negative("mock_interviews")
    core_subject_revisions = parse_non_negative("core_subject_revisions")
    company_rounds_reviewed = parse_non_negative("company_rounds_reviewed")
    weekly_hours_target = max(parse_non_negative("weekly_hours_target", 10), 1)
    weekly_hours_completed = parse_non_negative("weekly_hours_completed")
    focus_area = request.form.get("focus_area", "").strip()

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO preparation_progress(
            username, dsa_questions, aptitude_sets, mock_interviews,
            core_subject_revisions, company_rounds_reviewed,
            weekly_hours_target, weekly_hours_completed, focus_area, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(username) DO UPDATE SET
            dsa_questions=excluded.dsa_questions,
            aptitude_sets=excluded.aptitude_sets,
            mock_interviews=excluded.mock_interviews,
            core_subject_revisions=excluded.core_subject_revisions,
            company_rounds_reviewed=excluded.company_rounds_reviewed,
            weekly_hours_target=excluded.weekly_hours_target,
            weekly_hours_completed=excluded.weekly_hours_completed,
            focus_area=excluded.focus_area,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            session["user"],
            dsa_questions,
            aptitude_sets,
            mock_interviews,
            core_subject_revisions,
            company_rounds_reviewed,
            weekly_hours_target,
            weekly_hours_completed,
            focus_area,
        )
    )
    conn.commit()
    conn.close()

    flash("Preparation progress updated.", "success")
    return redirect("/dashboard#prep-progress")


@app.route("/contact-admin", methods=["POST"])
def contact_admin():

    if session.get("role") != "contributor":
        return redirect("/dashboard")

    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()

    if not subject or not message:
        flash("Subject and message are required.", "error")
        return redirect("/dashboard#contact-admin")

    if len(subject) > 120 or len(message) > 1000:
        flash("Keep subject under 120 chars and message under 1000 chars.", "error")
        return redirect("/dashboard#contact-admin")

    # Optional PDF attachment
    saved_file_path = None
    original_file_name = None
    attachment = request.files.get("attachment")
    if attachment and attachment.filename:
        original_file_name = secure_filename(attachment.filename)
        if not is_allowed_pdf(original_file_name):
            flash("Only PDF files are allowed as attachments.", "error")
            return redirect("/dashboard#contact-admin")
        unique_filename = f"{uuid.uuid4().hex}_{original_file_name}"
        try:
            attachment.save(os.path.join(CONTACT_UPLOAD_FOLDER, unique_filename))
            saved_file_path = unique_filename
        except Exception:
            flash("Failed to upload attachment. Please try again.", "error")
            return redirect("/dashboard#contact-admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO contributor_admin_contacts(contributor_username, subject, message, file_path, original_name)
        VALUES(?,?,?,?,?)
        """,
        (session["user"], subject, message, saved_file_path, original_file_name)
    )
    conn.commit()
    conn.close()

    flash("Message sent to admin successfully.", "success")
    return redirect("/dashboard#contact-admin")


@app.route("/contact-admin/file/<path:filename>")
def serve_contact_file(filename):
    if session.get("role") not in ["contributor", "admin"] and not is_admin_authenticated():
        return redirect("/dashboard")
    safe_name = secure_filename(os.path.basename(filename))
    return send_from_directory(CONTACT_UPLOAD_FOLDER, safe_name, as_attachment=False)


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

    viewer_name = get_active_viewer_username()

    if not viewer_name:
        return {"status": "unauthorized"}, 401

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    record_experience_view(cur, viewer_name, exp_id)

    conn.commit()
    conn.close()

    return {"status": "ok"}


@app.route("/experience/<int:exp_id>")
def experience_detail(exp_id):

    viewer_name = get_active_viewer_username()

    if not viewer_name:
        return redirect("/")

    home_url = url_for("dashboard") if "role" in session else url_for("admin_home")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute(
        """
        SELECT e.id, e.company, e.job_role, e.year, e.description, e.posted_by,
               e.interview_date, e.outcome, e.package_offered, e.views,
               u.role, u.designation
        FROM experiences e
        LEFT JOIN users u ON e.posted_by = u.username
        WHERE e.id=?
        """,
        (exp_id,)
    )
    experience = cur.fetchone()

    if not experience:
        conn.close()
        flash("Experience not found.", "error")
        return redirect(home_url)

    record_experience_view(cur, viewer_name, exp_id)

    cur.execute(
        """
        SELECT file_path, original_name, uploaded_at
        FROM experience_documents
        WHERE experience_id=?
        ORDER BY id DESC
        """,
        (exp_id,)
    )
    documents = cur.fetchall()

    is_verified = exp_id in get_verified_ids(cur)
    can_bookmark = "role" in session and session.get("role") != "admin"
    is_bookmarked = False

    if can_bookmark:
        cur.execute(
            "SELECT 1 FROM bookmarks WHERE username=? AND experience_id=?",
            (session["user"], exp_id)
        )
        is_bookmarked = cur.fetchone() is not None

    conn.commit()
    conn.close()

    posted_by_label = "Admin" if experience[10] == "admin" else ("You" if experience[5] == viewer_name else experience[5])
    view_count = (experience[9] or 0) + 1

    if experience[10] == "admin":
        role_display = "Admin"
    elif experience[10] == "contributor":
        role_display = experience[11] if experience[11] else "Contributor"
    else:
        role_display = "Student"

    return render_template(
        "experience_detail.html",
        experience=experience,
        posted_by_label=posted_by_label,
        role_display=role_display,
        is_verified=is_verified,
        documents=documents,
        can_bookmark=can_bookmark,
        is_bookmarked=is_bookmarked,
        home_url=home_url,
        company_url=url_for("company_page", name=experience[1]) if "role" in session else None,
        view_count=view_count
    )

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

    cur.execute(
        """
        SELECT id, contributor_username, subject, message, admin_reply, replied_at, created_at, file_path, original_name
        FROM contributor_admin_contacts
        ORDER BY id DESC
        LIMIT 30
        """
    )
    contributor_contacts = cur.fetchall()

    cur.execute(
        """
        SELECT name, date
        FROM companies
        ORDER BY date ASC, name ASC
        """
    )
    companies = cur.fetchall()

    conn.close()

    return render_template(
        "admin_manage.html",
        experiences=experiences,
        student_users=student_users,
        contributor_users=contributor_users,
        verified_ids=verified_ids,
        recent_notifications=recent_notifications,
        contributor_contacts=contributor_contacts,
        companies=companies
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


@app.route("/admin/contact/reply/<int:contact_id>", methods=["POST"])
def admin_reply_contact(contact_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    reply_text = request.form.get("reply", "").strip()
    if not reply_text:
        flash("Reply cannot be empty.", "error")
        return redirect("/admin/controls")

    if len(reply_text) > 1000:
        flash("Reply must be under 1000 characters.", "error")
        return redirect("/admin/controls")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE contributor_admin_contacts
        SET admin_reply=?, replied_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (reply_text, contact_id)
    )
    conn.commit()
    conn.close()

    flash("Reply sent to contributor message.", "success")
    return redirect("/admin/controls")


@app.route("/admin/contact/edit/<int:contact_id>", methods=["POST"])
def admin_edit_contact(contact_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()

    if not subject or not message:
        flash("Subject and message are required.", "error")
        return redirect("/admin/controls")

    if len(subject) > 120 or len(message) > 1000:
        flash("Keep subject under 120 chars and message under 1000 chars.", "error")
        return redirect("/admin/controls")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE contributor_admin_contacts
        SET subject=?, message=?
        WHERE id=?
        """,
        (subject, message, contact_id)
    )
    conn.commit()
    conn.close()

    flash("Contributor contact message updated.", "success")
    return redirect("/admin/controls")


@app.route("/admin/contact/delete/<int:contact_id>", methods=["POST"])
def admin_delete_contact(contact_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM contributor_admin_contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()

    flash("Contributor message deleted.", "success")
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


@app.route("/notifications/clear", methods=["POST"])
def clear_notifications():

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

    return {"status": "ok", "cleared_until": latest_visible_id}


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


@app.route("/admin/unverify_experience/<int:exp_id>", methods=["POST"])
def admin_unverify_experience(exp_id):

    if not is_admin_authenticated():
        return redirect("/login/admin")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM experience_verifications WHERE experience_id=?", (exp_id,))

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