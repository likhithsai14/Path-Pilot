from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "supersecretkey"


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
        approved INTEGER DEFAULT 1
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
        posted_by TEXT
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

    conn.commit()
    conn.close()

init_db()


# ---------------- HOME ---------------- #

@app.route("/")
def home():
    return render_template("home.html")


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
                return "Registration successful! Wait for admin approval."

            return redirect(url_for("login", role=role))

        except:
            conn.close()
            return "User already exists!"

    return render_template("register.html", role=role)


# ---------------- LOGIN ---------------- #

@app.route("/login/<role>", methods=["GET", "POST"])
def login(role):

    if request.method == "POST":

        username = request.form.get("username").lower().strip()
        password = request.form.get("password")

        # ADMIN LOGIN
        if role == "admin":
            if username == "admin@cmrcet.ac.in" and password == "admin123":
                session["user"] = "Admin"
                session["role"] = "admin"
                return redirect("/dashboard")
            else:
                return "Invalid Admin Credentials!"

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE username=? AND role=?", (username, role))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):

            if role == "contributor" and user[5] == 0:
                return "Your account is waiting for admin approval."

            session["user"] = username
            session["role"] = role
            session["designation"] = user[4]

            return redirect("/dashboard")

        return "Invalid credentials!"

    return render_template("login.html", role=role)


# ---------------- DASHBOARD ---------------- #

@app.route("/dashboard")
def dashboard():

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM experiences ORDER BY id DESC")
    experiences = cur.fetchall()

    total_exp = len(experiences)

    cur.execute("SELECT COUNT(DISTINCT company) FROM experiences")
    companies_count = cur.fetchone()[0]

    cur.execute("SELECT MAX(year) FROM experiences")
    latest_year = cur.fetchone()[0]

    if latest_year is None:
        latest_year = "-"

    pending_users = []
    if session["role"] == "admin":
        cur.execute("""
            SELECT id, username, designation
            FROM users
            WHERE role='contributor' AND approved=0
        """)
        pending_users = cur.fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        user=session["user"],
        role=session["role"],
        designation=session.get("designation"),
        experiences=experiences,
        pending_users=pending_users,
        total_exp=total_exp,
        companies_count=companies_count,
        latest_year=latest_year
    )


# ---------------- SEARCH ---------------- #

@app.route("/search", methods=["GET", "POST"])
def search():

    if "role" not in session:
        return redirect("/")

    results = []

    if request.method == "POST":

        keyword = request.form.get("keyword")

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        cur.execute("""
            SELECT * FROM experiences
            WHERE company LIKE ? OR description LIKE ?
        """, (f"%{keyword}%", f"%{keyword}%"))

        results = cur.fetchall()
        conn.close()

    return render_template("search.html", results=results)


# ---------------- COMPANIES ---------------- #

@app.route("/companies")
def companies():

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT name, date FROM companies")
    companies = cur.fetchall()

    conn.close()

    return render_template("companies.html", companies=companies)

@app.route("/company/<name>")
def company_page(name):

    if "role" not in session:
        return redirect("/")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM experiences WHERE company=?", (name,))
    experiences = cur.fetchall()

    conn.close()

    return render_template("company_page.html", company=name, experiences=experiences)


# ---------------- ADD COMPANY ---------------- #

@app.route("/admin/add_company", methods=["GET","POST"])
def add_company():

    if "role" not in session or session["role"] != "admin":
        return redirect("/dashboard")

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

# ---------------- SHARE EXPERIENCE ---------------- #

@app.route("/share", methods=["GET", "POST"])
def share():

    if "role" not in session or session["role"] not in ["contributor", "admin"]:
        return redirect("/dashboard")

    if request.method == "POST":

        company = request.form.get("company")
        job_role = request.form.get("job_role")
        year = request.form.get("year")
        description = request.form.get("description")

        conn = sqlite3.connect("database.db")
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO experiences(company,job_role,year,description,posted_by)
            VALUES(?,?,?,?,?)
        """, (company, job_role, year, description, session["user"]))

        conn.commit()
        conn.close()

        return redirect("/dashboard")

    return render_template("share.html")


# ---------------- APPROVE ---------------- #

@app.route("/approve/<int:user_id>")
def approve(user_id):

    if "role" not in session or session["role"] != "admin":
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/dashboard")


# ---------------- REJECT ---------------- #

@app.route("/reject/<int:user_id>")
def reject(user_id):

    if "role" not in session or session["role"] != "admin":
        return redirect("/dashboard")

    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/dashboard")


# ---------------- LOGOUT ---------------- #

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)