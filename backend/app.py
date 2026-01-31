from flask import Flask, render_template, request, jsonify, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import time

import os

app = Flask(__name__)

# ✅ stable secret key (set SECRET_KEY in Render env vars)
app.secret_key = os.environ.get("SECRET_KEY", "civicfix_secret_key")

# ✅ cookies work on Render (https)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True
)

# ✅ ALWAYS point to the DB inside backend folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "civicfix.db")


# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    # Create users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    # Create complaints table
    conn.execute("""
    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        title TEXT,
        description TEXT,
        category TEXT,
        priority TEXT,
        latitude REAL,
        longitude REAL,
        location_text TEXT,
        image TEXT,
        voice TEXT,
        assigned_department TEXT,
        status TEXT DEFAULT 'Pending',
        created_at TEXT
    )
""")

    conn.commit()
    conn.close()

def ensure_columns():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(complaints)")
    cols = {row["name"] for row in cur.fetchall()}

    def add_col(name, coltype):
        if name not in cols:
            cur.execute(f"ALTER TABLE complaints ADD COLUMN {name} {coltype}")

    add_col("latitude", "REAL")
    add_col("longitude", "REAL")
    add_col("voice", "TEXT")
    add_col("assigned_department", "TEXT")
    add_col("location_text", "TEXT")

    conn.commit()
    conn.close()

init_db()
ensure_columns()

# Call init_db once when app starts

@app.route("/update-status/<int:id>", methods=["POST"])
def update_status(id):
    data = request.get_json()
    status = data.get("status")
    if not status:
        return jsonify({"success": False}), 400

    conn = get_db()
    conn.execute("UPDATE complaints SET status=? WHERE id=?", (status, id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


from datetime import datetime
import os
from flask import url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER



@app.route("/submit-complaint", methods=["POST"])
def submit_complaint():
    conn = get_db()
    cursor = conn.cursor()

    username = session.get("username", "Anonymous")

    title = request.form.get("title", "")
    description = request.form.get("description", "")
    category = request.form.get("category", "")
    priority = request.form.get("priority", "")

    lat = request.form.get("lat")
    lng = request.form.get("lng")
    location_text = request.form.get("location_text", "")

    image = request.files.get("image")
    voice = request.files.get("voice")

    img_name = None
    voice_name = None

    if image:
        img_name = f"img_{int(time.time())}.jpg"
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], img_name))

    if voice:
        voice_name = f"voice_{int(time.time())}.webm"
        voice.save(os.path.join(app.config["UPLOAD_FOLDER"], voice_name))

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO complaints
        (username, title, description, category, priority, latitude, longitude, location_text, image, voice, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)
    """, (
        username, title, description, category, priority,
        float(lat) if lat else None,
        float(lng) if lng else None,
        location_text,
        img_name, voice_name,
        created_at
    ))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Complaint submitted successfully"})



@app.route("/get-complaints")
def get_complaints():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM complaints
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    result = []  # ✅ DEFINE result

    for c in rows:  # ✅ DEFINE c
        result.append({
            "id": c["id"],
            "username": c["username"],
            "title": c["title"],
            "description": c["description"],
            "category": c["category"],
            "priority": c["priority"],
            "lat": c["latitude"],
            "lng": c["longitude"],
            "location_text": c["location_text"],
            "image": c["image"],
            "voice": c["voice"],
            "assigned_department": c["assigned_department"],
            "status": c["status"],
            "created_at": c["created_at"]
        })

    return jsonify(result)


from flask import send_from_directory

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------- ROUTES ----------------

@app.route("/")
def sign():
    if "username" in session:
        return redirect("/home")
    return render_template("sign.html")



# ---------- SIGN UP ----------
@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    raw_password = data.get("password") or ""

    if not username or not email or not raw_password:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    password = generate_password_hash(raw_password)

    conn = get_db()
    cur = conn.cursor()

    if cur.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        return jsonify({"success": False, "message": "User already exists"})

    cur.execute(
        "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
        (username, email, password)
    )
    conn.commit()
    conn.close()

    session["username"] = username
    session["email"] = email
    session["admin"] = False

    return jsonify({"success": True})

# ---------- SIGN IN ----------
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    # ---- ADMIN CHECK ----
    if email == "admin@civicfix.com" and password == "admin123":
        session["admin"] = True
        session["username"] = "Admin"
        session["email"] = email
        return jsonify({"success": True, "role": "admin"})

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()

    if user and check_password_hash(user["password"], password):
        session["username"] = user["username"]
        session["email"] = user["email"]
        session["admin"] = False
        return jsonify({"success": True, "role": "user"})

    return jsonify({"success": False})

@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/")
    return render_template("admin.html")


# ---------- HOME ----------
@app.route("/home")
def home():
    if "username" not in session:
        return redirect("/")
    return render_template("home.html", username=session["username"])

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/")

    conn = get_db()
    complaints = conn.execute("""
        SELECT * FROM complaints
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    return render_template("dashboard.html", username=session["username"], complaints=complaints)

# ---------- REPORT ----------
@app.route("/report")
def report():
    if "username" not in session:
        return redirect("/")
    return render_template("report.html")

# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
