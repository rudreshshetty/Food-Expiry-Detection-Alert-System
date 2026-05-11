import os
import pickle
import sqlite3
from datetime import date, datetime

import numpy as np
try:
    import mysql.connector
except ImportError:
    mysql = None

try:
    from pymongo import MongoClient
    from bson.objectid import ObjectId
except ImportError:
    MongoClient = None
    ObjectId = None

try:
    from twilio.rest import Client
except ImportError:
    Client = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from werkzeug.security import check_password_hash, generate_password_hash

DB_DRIVER = None
CATEGORIES = ["Dairy", "Grocery", "Snacks", "Beverages"]

app = Flask(__name__)
if load_dotenv is not None:
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(dotenv_path)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key")

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


def get_db_connection():
    global DB_DRIVER
    mongo_uri = os.environ.get("MONGO_URI") or "mongodb://localhost:27017"
    mongo_db_name = os.environ.get("MONGO_DB", "food_expiry")
    db_host = os.environ.get("DB_HOST")
    db_user = os.environ.get("DB_USER")
    db_password = os.environ.get("DB_PASSWORD", "")
    db_name = os.environ.get("DB_NAME", "food_expiry")

    if MongoClient is not None:
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            client.admin.command("ping")
            db = client[mongo_db_name]
            DB_DRIVER = "mongo"
            return db
        except Exception as exc:
            print(f"MongoDB connection failed: {exc}")

    if db_host and db_user and mysql is not None:
        try:
            conn = mysql.connector.connect(
                host=db_host,
                user=db_user,
                password=db_password,
                database=db_name,
                autocommit=True,
            )
            DB_DRIVER = "mysql"
            return conn
        except Exception:
            pass

    db_path = os.path.join(os.path.dirname(__file__), "food_expiry.db")
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    DB_DRIVER = "sqlite"
    return conn


def is_mongo():
    return DB_DRIVER == "mongo"


def mongo_object_id(value):
    if ObjectId is None:
        return value
    try:
        return ObjectId(value)
    except Exception:
        return value


def normalize_item(item):
    if is_mongo():
        item["id"] = str(item.get("_id"))
        item.pop("_id", None)
        expiry_date = item.get("expiry_date")
        if isinstance(expiry_date, str):
            try:
                item["expiry_date"] = date.fromisoformat(expiry_date[:10])
            except Exception:
                pass
        elif isinstance(expiry_date, datetime):
            item["expiry_date"] = expiry_date.date()
    else:
        item["id"] = item.get("id")
    return item


def normalize_phone(phone):
    if not phone:
        return None
    phone = str(phone).strip()
    if phone.startswith("+"):
        return phone
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return phone


def get_twilio_settings():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    to_number = normalize_phone(os.environ.get("TWILIO_TO_NUMBER") or "8792179081")
    return account_sid, auth_token, from_number, to_number


def send_sms(body, to_number=None):
    account_sid, auth_token, from_number, default_to = get_twilio_settings()
    to_number = normalize_phone(to_number) if to_number else default_to

    if not all([account_sid, auth_token, from_number, to_number, Client]):
        print("Twilio is not configured or not installed. SMS skipped.")
        return False

    try:
        client = Client(account_sid, auth_token)
        message = client.messages.create(body=body, from_=from_number, to=to_number)
        print(f"SMS sent to {to_number}: {message.sid}")
        return True
    except Exception as exc:
        print(f"Failed to send SMS to {to_number}: {exc}")
        return False


def sql_query(query_string):
    if DB_DRIVER == "sqlite":
        return query_string.replace("%s", "?")
    return query_string


def get_storage_temp(category: str) -> float:
    category = (category or "").strip().title()
    if category == "Dairy":
        return 5.0
    if category == "Beverages":
        return 10.0
    return 25.0


def get_cursor(conn):
    if DB_DRIVER == "mysql":
        return conn.cursor(dictionary=True)
    return conn.cursor()


def init_db():
    conn = get_db_connection()
    if DB_DRIVER == "mongo":
        conn.users.create_index("username", unique=True)
        conn.items.create_index([("user_id", 1), ("expiry_date", 1)])
        conn.settings.create_index("user_id", unique=True)

        # Delete existing testuser and recreate with fresh password
        conn.users.delete_one({"username": "testuser"})
        conn.settings.delete_one({"user_id": "testuser"})
        
        hashed = generate_password_hash("Test@1234")
        print(f"DEBUG: Creating testuser with password hash: {hashed[:20]}...")
        conn.users.insert_one({"username": "testuser", "password": hashed})
        conn.settings.insert_one(
            {
                "user_id": "testuser",
                "phone": "+918792179081",
                "sms_enabled": True,
                "expired_alert": True,
                "expiring_alert": True,
                "ml_alert": True,
                "alert_days": 2,
                "storage_mode": "Fridge",
                "appearance": "Light",
            }
        )
        return

    cursor = conn.cursor()
    if DB_DRIVER == "sqlite":
        cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute(sql_query(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username VARCHAR(50) NOT NULL UNIQUE, "
        "password VARCHAR(255) NOT NULL)"
    ))
    cursor.execute(sql_query(
        "CREATE TABLE IF NOT EXISTS items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "name VARCHAR(100) NOT NULL, "
        "expiry_date DATE NOT NULL, "
        "category VARCHAR(50) DEFAULT 'Grocery', "
        "days_since_purchase INT DEFAULT 0, "
        "storage_temp FLOAT DEFAULT 25.0, "
        "predicted_days FLOAT DEFAULT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"
    ))

    if DB_DRIVER == "sqlite":
        cursor.execute("PRAGMA table_info(items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "category" not in columns:
            cursor.execute("ALTER TABLE items ADD COLUMN category VARCHAR(50) DEFAULT 'Grocery'")
    elif DB_DRIVER == "mysql":
        cursor.execute("SHOW COLUMNS FROM items LIKE 'category'")
        if cursor.fetchone() is None:
            cursor.execute("ALTER TABLE items ADD COLUMN category VARCHAR(50) DEFAULT 'Grocery'")

    if DB_DRIVER == "sqlite":
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL UNIQUE, "
            "phone VARCHAR(25), "
            "sms_enabled BOOLEAN DEFAULT 0, "
            "expired_alert BOOLEAN DEFAULT 1, "
            "expiring_alert BOOLEAN DEFAULT 1, "
            "ml_alert BOOLEAN DEFAULT 1, "
            "alert_days INTEGER DEFAULT 2, "
            "storage_mode VARCHAR(20) DEFAULT 'Fridge', "
            "appearance VARCHAR(10) DEFAULT 'Light', "
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"
        )
    else:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "id INT PRIMARY KEY AUTO_INCREMENT, "
            "user_id INT NOT NULL UNIQUE, "
            "phone VARCHAR(25), "
            "sms_enabled TINYINT(1) DEFAULT 0, "
            "expired_alert TINYINT(1) DEFAULT 1, "
            "expiring_alert TINYINT(1) DEFAULT 1, "
            "ml_alert TINYINT(1) DEFAULT 1, "
            "alert_days INT DEFAULT 2, "
            "storage_mode VARCHAR(20) DEFAULT 'Fridge', "
            "appearance VARCHAR(10) DEFAULT 'Light', "
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"
        )

    cursor.execute(sql_query("SELECT id FROM users WHERE username = %s"), ("testuser",))
    user_row = cursor.fetchone()
    if user_row is None:
        cursor.execute(
            sql_query("INSERT INTO users (username, password) VALUES (%s, %s)"),
            ("testuser", generate_password_hash("Test@1234")),
        )
        conn.commit()
        cursor.execute(sql_query("SELECT id FROM users WHERE username = %s"), ("testuser",))
        user_row = cursor.fetchone()

    user_id = user_row["id"] if user_row is not None else None
    if user_id is not None:
        cursor.execute(sql_query("SELECT id FROM settings WHERE user_id = %s"), (user_id,))
        if cursor.fetchone() is None:
            cursor.execute(
                sql_query(
                    "INSERT INTO settings (user_id, phone, sms_enabled, expired_alert, expiring_alert, ml_alert, alert_days, storage_mode, appearance) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ),
                (user_id, '', 0, 1, 1, 1, 2, 'Fridge', 'Light'),
            )
    conn.commit()
    cursor.close()
    conn.close()


class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password


@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user = None
    if DB_DRIVER == "mongo":
        user = conn.users.find_one({"username": user_id})
    else:
        cursor = get_cursor(conn)
        cursor.execute(sql_query("SELECT * FROM users WHERE id = %s"), (user_id,))
        user = cursor.fetchone()
        cursor.close()

    if DB_DRIVER != "mongo":
        conn.close()
    if user:
        if DB_DRIVER == "mongo":
            return User(user["username"], user["username"], user["password"])
        return User(user["id"], user["username"], user["password"])
    return None


def load_user_settings(user_id):
    conn = get_db_connection()
    row = None
    if DB_DRIVER == "mongo":
        row = conn.settings.find_one({"user_id": user_id})
    else:
        cursor = get_cursor(conn)
        cursor.execute(sql_query("SELECT * FROM settings WHERE user_id = %s"), (user_id,))
        row = cursor.fetchone()
        cursor.close()

    if DB_DRIVER != "mongo":
        conn.close()
    if not row:
        return {
            "phone": "",
            "sms_enabled": False,
            "expired_alert": True,
            "expiring_alert": True,
            "ml_alert": True,
            "alert_days": 2,
            "storage_mode": "Fridge",
            "appearance": "Light",
        }

    return {
        "phone": row["phone"],
        "sms_enabled": bool(row["sms_enabled"]),
        "expired_alert": bool(row["expired_alert"]),
        "expiring_alert": bool(row["expiring_alert"]),
        "ml_alert": bool(row["ml_alert"]),
        "alert_days": int(row.get("alert_days") or 2),
        "storage_mode": row.get("storage_mode") or "Fridge",
        "appearance": row.get("appearance") or "Light",
    }


def get_items_for_user(user_id):
    conn = get_db_connection()
    items = []
    if DB_DRIVER == "mongo":
        cursor = conn.items.find({"user_id": user_id}).sort("expiry_date", 1)
        for item in cursor:
            items.append(normalize_item(item))
        return items

    cursor = get_cursor(conn)
    cursor.execute(
        sql_query(
            "SELECT id, name, expiry_date, category, days_since_purchase, storage_temp, predicted_days, created_at "
            "FROM items WHERE user_id = %s ORDER BY expiry_date ASC"
        ),
        (user_id,),
    )
    fetched = cursor.fetchall()
    cursor.close()
    conn.close()
    return [normalize_item(dict(item)) for item in fetched]


@app.context_processor
def inject_theme_settings():
    if current_user.is_authenticated:
        return {"app_settings": load_user_settings(current_user.id)}
    return {"app_settings": {"appearance": "Light"}}


@app.route("/settings", methods=["POST"])
@login_required
def save_settings():
    username = request.form.get("username", "").strip()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    sms_enabled = 1 if request.form.get("sms_enabled") == "on" else 0
    expired_alert = 1 if request.form.get("expired_alert") == "on" else 0
    expiring_alert = 1 if request.form.get("expiring_alert") == "on" else 0
    ml_alert = 1 if request.form.get("ml_alert") == "on" else 0
    try:
        alert_days = int(request.form.get("alert_days", 2))
    except ValueError:
        alert_days = 2
    storage_mode = request.form.get("storage_mode", "Fridge")
    appearance = request.form.get("appearance", "Light")

    conn = get_db_connection()
    if DB_DRIVER == "mongo":
        if username and username != current_user.id:
            conn.users.update_one({"username": current_user.id}, {"$set": {"username": username}})
            conn.items.update_many({"user_id": current_user.id}, {"$set": {"user_id": username}})
            conn.settings.update_one({"user_id": current_user.id}, {"$set": {"user_id": username}})
            current_user.id = username
        if password:
            conn.users.update_one(
                {"username": current_user.id},
                {"$set": {"password": generate_password_hash(password)}},
            )

        update_values = {
            "phone": phone,
            "sms_enabled": bool(sms_enabled),
            "expired_alert": bool(expired_alert),
            "expiring_alert": bool(expiring_alert),
            "ml_alert": bool(ml_alert),
            "alert_days": alert_days,
            "storage_mode": storage_mode,
            "appearance": appearance,
        }
        result = conn.settings.update_one(
            {"user_id": current_user.id},
            {"$set": update_values},
            upsert=True,
        )
        flash("Settings saved successfully.")
        return redirect(url_for("dashboard") + "#settings-section")

    cursor = get_cursor(conn)
    if username:
        cursor.execute(sql_query("UPDATE users SET username = %s WHERE id = %s"), (username, current_user.id))
    if password:
        cursor.execute(sql_query("UPDATE users SET password = %s WHERE id = %s"), (generate_password_hash(password), current_user.id))

    cursor.execute(sql_query("SELECT id FROM settings WHERE user_id = %s"), (current_user.id,))
    setting_row = cursor.fetchone()
    if setting_row:
        cursor.execute(
            sql_query(
                "UPDATE settings SET phone = %s, sms_enabled = %s, expired_alert = %s, expiring_alert = %s, ml_alert = %s, alert_days = %s, storage_mode = %s, appearance = %s WHERE user_id = %s"
            ),
            (phone, sms_enabled, expired_alert, expiring_alert, ml_alert, alert_days, storage_mode, appearance, current_user.id),
        )
    else:
        cursor.execute(
            sql_query(
                "INSERT INTO settings (user_id, phone, sms_enabled, expired_alert, expiring_alert, ml_alert, alert_days, storage_mode, appearance) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            (current_user.id, phone, sms_enabled, expired_alert, expiring_alert, ml_alert, alert_days, storage_mode, appearance),
        )

    conn.commit()
    cursor.close()
    conn.close()
    flash("Settings saved successfully.")
    return redirect(url_for("dashboard") + "#settings-section")


def load_ml_model():
    model_path = os.path.join(os.path.dirname(__file__), "model.pkl")
    try:
        with open(model_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


model = load_ml_model()
init_db()


def predict_shelf_life(days_since_purchase, storage_temp):
    if model is None:
        return None
    try:
        X = np.array([[days_since_purchase, storage_temp]], dtype=float)
        predicted = float(model.predict(X)[0])
        return max(predicted, 0.0)
    except Exception:
        return None


def get_ml_status(predicted_days):
    if predicted_days is None:
        return "Unknown"
    if predicted_days < 1:
        return "High Risk"
    if predicted_days < 3:
        return "Consume Soon"
    return "Safe"


def calculate_status(expiry_date):
    days_left = (expiry_date - date.today()).days
    if days_left < 0:
        return "Expired", days_left
    if days_left <= 2:
        return "Expiring Soon", days_left
    return "Fresh", days_left


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Please enter both username and password.")
            return redirect(url_for("register"))

        conn = get_db_connection()
        if DB_DRIVER == "mongo":
            if conn.users.find_one({"username": username}):
                flash("Username already exists. Choose another one.")
                return redirect(url_for("register"))
            conn.users.insert_one({"username": username, "password": generate_password_hash(password)})
            conn.settings.insert_one(
                {
                    "user_id": username,
                    "phone": "",
                    "sms_enabled": False,
                    "expired_alert": True,
                    "expiring_alert": True,
                    "ml_alert": True,
                    "alert_days": 2,
                    "storage_mode": "Fridge",
                    "appearance": "Light",
                }
            )
            flash("Account created successfully. Please log in.")
            return redirect(url_for("login"))

        cursor = get_cursor(conn)
        cursor.execute(sql_query("SELECT id FROM users WHERE username = %s"), (username,))
        if cursor.fetchone():
            flash("Username already exists. Choose another one.")
            cursor.close()
            conn.close()
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)
        cursor.execute(
            sql_query("INSERT INTO users (username, password) VALUES (%s, %s)"),
            (username, hashed_password),
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash("Account created successfully. Please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        print(f"DEBUG: Login attempt - username={username}, password_length={len(password)}")
        
        conn = get_db_connection()
        row = None
        if DB_DRIVER == "mongo":
            row = conn.users.find_one({"username": username})
        else:
            cursor = get_cursor(conn)
            cursor.execute(sql_query("SELECT * FROM users WHERE username = %s"), (username,))
            row = cursor.fetchone()
            cursor.close()
            conn.close()

        print(f"DEBUG: User found in DB: {row is not None}")
        if row:
            print(f"DEBUG: DB_DRIVER={DB_DRIVER}, row keys={list(row.keys()) if hasattr(row, 'keys') else 'N/A'}")
            stored_hash = row["password"]
            is_valid = check_password_hash(stored_hash, password)
            print(f"DEBUG: Password check result: {is_valid}")
        
        if row and check_password_hash(row["password"], password):
            user_id = username if DB_DRIVER == "mongo" else row["id"]
            user = User(user_id, row["username"], row["password"])
            login_user(user)
            print(f"DEBUG: Login successful for user {user_id}")
            return redirect(url_for("dashboard"))

        print(f"DEBUG: Login failed for user {username}")
        flash("Invalid username or password.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    items = get_items_for_user(current_user.id)
    settings = load_user_settings(current_user.id)
    filter_category = request.args.get("filter", "")
    if filter_category:
        items = [item for item in items if item.get("category") == filter_category]

    summary = {"Fresh": 0, "Expiring Soon": 0, "Expired": 0}
    ml_summary = {"Safe": 0, "Consume Soon": 0, "High Risk": 0, "Unknown": 0}
    category_counts = {category: 0 for category in CATEGORIES}
    expiry_timeline = {}
    days_distribution = {"0-2": 0, "3-5": 0, "6+": 0}
    category_risk = {cat: {"Safe": 0, "Consume Soon": 0, "High Risk": 0, "Unknown": 0} for cat in CATEGORIES}
    weekly_waste = 0
    alert_items = []
    expiry_vs_prediction = []
    storage_condition = {"Refrigerated (0-8°C)": 0, "Room Temp (9-25°C)": 0, "Warm (>25°C)": 0}

    for item in items:
        status, days_left = calculate_status(item["expiry_date"])
        item["status"] = status
        item["days_left"] = days_left

        if item.get("predicted_days") is None and item.get("days_since_purchase") is not None:
            temp = item.get("storage_temp") or get_storage_temp(item.get("category"))
            item["predicted_days"] = predict_shelf_life(
                item["days_since_purchase"], temp
            )

        if item.get("predicted_days") is not None:
            item["predicted_days"] = round(item["predicted_days"], 1)

        item["ml_status"] = get_ml_status(item.get("predicted_days"))

        summary[status] += 1
        if item["ml_status"] in ml_summary:
            ml_summary[item["ml_status"]] += 1

        category_counts[item.get("category") or "Grocery"] = category_counts.get(item.get("category") or "Grocery", 0) + 1

        # Expiry Timeline
        expiry_date_str = item["expiry_date"].strftime("%Y-%m-%d")
        expiry_timeline[expiry_date_str] = expiry_timeline.get(expiry_date_str, 0) + 1

        # Days Distribution
        if days_left <= settings.get("alert_days", 2) and days_left >= 0:
            days_distribution["0-2"] += 1
        elif days_left <= 5:
            days_distribution["3-5"] += 1
        else:
            days_distribution["6+"] += 1

        # Category vs Risk
        cat = item.get("category") or "Grocery"
        if cat in category_risk and item["ml_status"] in category_risk[cat]:
            category_risk[cat][item["ml_status"]] += 1

        # Weekly Waste
        if days_left <= 7:
            weekly_waste += 1

        if status != "Fresh" or item["ml_status"] in ("High Risk", "Consume Soon"):
            alert_items.append(item)

        if item.get("predicted_days") is not None:
            expiry_vs_prediction.append(
                {
                    "expiry_date": item["expiry_date"].strftime("%Y-%m-%d"),
                    "predicted_days": item["predicted_days"],
                }
            )

        # Storage condition
        temp = item.get("storage_temp") if item.get("storage_temp") is not None else get_storage_temp(item.get("category"))
        if temp <= 8:
            storage_condition["Refrigerated (0-8°C)"] += 1
        elif temp <= 25:
            storage_condition["Room Temp (9-25°C)"] += 1
        else:
            storage_condition["Warm (>25°C)"] += 1

    # Sort expiry timeline by date
    expiry_timeline = dict(sorted(expiry_timeline.items()))

    expired_items = [item for item in items if item["days_left"] < 0] if settings.get("expired_alert") else []
    expiring_soon_items = [item for item in items if 0 <= item["days_left"] <= settings.get("alert_days", 2)] if settings.get("expiring_alert") else []
    high_risk_items = [item for item in items if item["ml_status"] == "High Risk"] if settings.get("ml_alert") else []

    sms_log = []
    if settings.get("sms_enabled"):
        sms_log.extend([
            {"name": item["name"], "status": "Expired", "message": f"SMS sent for {item['name']}"}
            for item in expired_items
        ])
        sms_log.extend([
            {"name": item["name"], "status": "Expiring Soon", "message": f"SMS sent for {item['name']}"}
            for item in expiring_soon_items
        ])

    total_items = len(items)
    expired_items_count = summary["Expired"]
    waste_percentage = round((expired_items_count / total_items) * 100, 1) if total_items else 0

    week_report_labels = ["Last 7 Days", "7-14 Days", "14-21 Days", "21-28 Days"]
    week_report_values = [
        sum(1 for item in items if -7 < item["days_left"] <= 0),
        sum(1 for item in items if -14 < item["days_left"] <= -7),
        sum(1 for item in items if -21 < item["days_left"] <= -14),
        sum(1 for item in items if -28 < item["days_left"] <= -21),
    ]

    top_high_risk_items = sorted(
        [item for item in items if item["ml_status"] == "High Risk"],
        key=lambda item: item["days_left"]
    )[:5]

    return render_template(
        "dashboard.html",
        items=items,
        summary=summary,
        ml_summary=ml_summary,
        category_labels=list(category_counts.keys()),
        category_values=list(category_counts.values()),
        categories=CATEGORIES,
        selected_filter=filter_category,
        expiry_timeline_labels=list(expiry_timeline.keys()),
        expiry_timeline_values=list(expiry_timeline.values()),
        days_distribution_labels=list(days_distribution.keys()),
        days_distribution_values=list(days_distribution.values()),
        category_risk=category_risk,
        weekly_waste=weekly_waste,
        storage_labels=list(storage_condition.keys()),
        storage_values=list(storage_condition.values()),
        expired_items=expired_items,
        expiring_soon_items=expiring_soon_items,
        high_risk_items=high_risk_items,
        sms_log=sms_log,
        total_items=total_items,
        expired_items_count=expired_items_count,
        waste_percentage=waste_percentage,
        week_report_labels=week_report_labels,
        week_report_values=week_report_values,
        top_high_risk_items=top_high_risk_items,
        settings=settings,
        current_date=date.today().strftime("%d %b, %Y"),
        alert_needed=bool(alert_items),
        expiry_vs_prediction=expiry_vs_prediction,
    )


@app.route("/food-items")
@login_required
def food_items():
    items = get_items_for_user(current_user.id)

    summary = {"Fresh": 0, "Expiring Soon": 0, "Expired": 0}
    for item in items:
        status, days_left = calculate_status(item["expiry_date"])
        item["status"] = status
        item["days_left"] = days_left

        if item.get("predicted_days") is None and item.get("days_since_purchase") is not None:
            temp = item.get("storage_temp") or get_storage_temp(item.get("category"))
            item["predicted_days"] = predict_shelf_life(item["days_since_purchase"], temp)

        if item.get("predicted_days") is not None:
            item["predicted_days"] = round(item["predicted_days"], 1)

        item["ml_status"] = get_ml_status(item.get("predicted_days"))
        summary[status] += 1

    return render_template("food_items.html", items=items, summary=summary, categories=CATEGORIES)


@app.route("/add-item")
@login_required
def add_item_page():
    return render_template("add_item.html", categories=CATEGORIES)


@app.route("/reports")
@login_required
def reports():
    items = get_items_for_user(current_user.id)
    settings = load_user_settings(current_user.id)

    summary = {"Fresh": 0, "Expiring Soon": 0, "Expired": 0}
    ml_summary = {"Safe": 0, "Consume Soon": 0, "High Risk": 0, "Unknown": 0}
    category_counts = {category: 0 for category in CATEGORIES}
    expiry_timeline = {}
    days_distribution = {"0-2": 0, "3-5": 0, "6+": 0}
    category_risk = {cat: {"Safe": 0, "Consume Soon": 0, "High Risk": 0, "Unknown": 0} for cat in CATEGORIES}
    weekly_waste = 0
    expiry_vs_prediction = []
    storage_condition = {"Refrigerated (0-8°C)": 0, "Room Temp (9-25°C)": 0, "Warm (>25°C)": 0}

    for item in items:
        status, days_left = calculate_status(item["expiry_date"])
        item["status"] = status
        item["days_left"] = days_left

        if item.get("predicted_days") is None and item.get("days_since_purchase") is not None:
            temp = item.get("storage_temp") or get_storage_temp(item.get("category"))
            item["predicted_days"] = predict_shelf_life(item["days_since_purchase"], temp)

        if item.get("predicted_days") is not None:
            item["predicted_days"] = round(item["predicted_days"], 1)

        item["ml_status"] = get_ml_status(item.get("predicted_days"))

        summary[status] += 1
        if item["ml_status"] in ml_summary:
            ml_summary[item["ml_status"]] += 1

        category_counts[item.get("category") or "Grocery"] = category_counts.get(item.get("category") or "Grocery", 0) + 1

        expiry_date_str = item["expiry_date"].strftime("%Y-%m-%d")
        expiry_timeline[expiry_date_str] = expiry_timeline.get(expiry_date_str, 0) + 1

        if days_left <= settings.get("alert_days", 2) and days_left >= 0:
            days_distribution["0-2"] += 1
        elif days_left <= 5:
            days_distribution["3-5"] += 1
        else:
            days_distribution["6+"] += 1

        cat = item.get("category") or "Grocery"
        if cat in category_risk and item["ml_status"] in category_risk[cat]:
            category_risk[cat][item["ml_status"]] += 1

        if days_left <= 7:
            weekly_waste += 1

        if item.get("predicted_days") is not None:
            expiry_vs_prediction.append({"expiry_date": item["expiry_date"].strftime("%Y-%m-%d"), "predicted_days": item["predicted_days"]})

        temp = item.get("storage_temp") if item.get("storage_temp") is not None else get_storage_temp(item.get("category"))
        if temp <= 8:
            storage_condition["Refrigerated (0-8°C)"] += 1
        elif temp <= 25:
            storage_condition["Room Temp (9-25°C)"] += 1
        else:
            storage_condition["Warm (>25°C)"] += 1

    expiry_timeline = dict(sorted(expiry_timeline.items()))

    top_high_risk_items = sorted([item for item in items if item["ml_status"] == "High Risk"], key=lambda item: item["days_left"])[:5]

    total_items = len(items)
    expired_items_count = summary["Expired"]
    waste_percentage = round((expired_items_count / total_items) * 100, 1) if total_items else 0

    week_report_labels = ["Last 7 Days", "7-14 Days", "14-21 Days", "21-28 Days"]
    week_report_values = [
        sum(1 for item in items if -7 < item["days_left"] <= 0),
        sum(1 for item in items if -14 < item["days_left"] <= -7),
        sum(1 for item in items if -21 < item["days_left"] <= -14),
        sum(1 for item in items if -28 < item["days_left"] <= -21),
    ]

    return render_template(
        "reports.html",
        items=items,
        summary=summary,
        ml_summary=ml_summary,
        category_labels=list(category_counts.keys()),
        category_values=list(category_counts.values()),
        expiry_timeline_labels=list(expiry_timeline.keys()),
        expiry_timeline_values=list(expiry_timeline.values()),
        category_risk=category_risk,
        weekly_waste=weekly_waste,
        storage_labels=list(storage_condition.keys()),
        storage_values=list(storage_condition.values()),
        total_items=total_items,
        expired_items_count=expired_items_count,
        waste_percentage=waste_percentage,
        week_report_labels=week_report_labels,
        week_report_values=week_report_values,
        top_high_risk_items=top_high_risk_items,
        settings=settings,
        current_date=date.today().strftime("%d %b, %Y"),
        expiry_vs_prediction=expiry_vs_prediction,
    )


@app.route("/alerts")
@login_required
def alerts():
    items = get_items_for_user(current_user.id)
    settings = load_user_settings(current_user.id)

    summary = {"Fresh": 0, "Expiring Soon": 0, "Expired": 0}
    ml_summary = {"Safe": 0, "Consume Soon": 0, "High Risk": 0, "Unknown": 0}
    sms_log = []

    for item in items:
        status, days_left = calculate_status(item["expiry_date"])
        item["status"] = status
        item["days_left"] = days_left

        if item.get("predicted_days") is None and item.get("days_since_purchase") is not None:
            temp = item.get("storage_temp") or get_storage_temp(item.get("category"))
            item["predicted_days"] = predict_shelf_life(item["days_since_purchase"], temp)

        if item.get("predicted_days") is not None:
            item["predicted_days"] = round(item["predicted_days"], 1)

        item["ml_status"] = get_ml_status(item.get("predicted_days"))

        summary[status] += 1
        if item["ml_status"] in ml_summary:
            ml_summary[item["ml_status"]] += 1

    expired_items = [item for item in items if item["days_left"] < 0] if settings.get("expired_alert") else []
    expiring_soon_items = [item for item in items if 0 <= item["days_left"] <= settings.get("alert_days", 2)] if settings.get("expiring_alert") else []
    high_risk_items = [item for item in items if item["ml_status"] == "High Risk"] if settings.get("ml_alert") else []

    if settings.get("sms_enabled"):
        sms_log.extend([{"name": item["name"], "status": "Expired", "message": f"SMS sent for {item['name']}"} for item in expired_items])
        sms_log.extend([{"name": item["name"], "status": "Expiring Soon", "message": f"SMS sent for {item['name']}"} for item in expiring_soon_items])

    return render_template(
        "alerts.html",
        expired_items=expired_items,
        expiring_soon_items=expiring_soon_items,
        high_risk_items=high_risk_items,
        sms_log=sms_log,
        summary=summary,
        ml_summary=ml_summary,
        settings=settings,
    )


@app.route("/settings", methods=["GET"])
@login_required
def settings_page():
    settings = load_user_settings(current_user.id)
    return render_template("settings.html", settings=settings)


@app.route("/add", methods=["POST"])
@login_required
def add_item():
    name = request.form.get("name", "").strip()
    expiry_date = request.form.get("expiry_date", "")
    days_since_purchase = request.form.get("days_since_purchase", "0")
    category = request.form.get("category", "Grocery").strip().title()
    if category not in CATEGORIES:
        category = "Grocery"
    storage_temp = request.form.get("storage_temp", "")

    if not name or not expiry_date or not category:
        flash("Please provide food name, expiry date, and category.")
        return redirect(url_for("dashboard"))

    try:
        days_since_purchase = int(days_since_purchase)
    except ValueError:
        days_since_purchase = 0

    try:
        storage_temp = float(storage_temp)
    except ValueError:
        storage_temp = get_storage_temp(category)
    if not storage_temp:
        storage_temp = get_storage_temp(category)

    predicted_days = predict_shelf_life(days_since_purchase, storage_temp)

    try:
        conn = get_db_connection()
        if DB_DRIVER == "mongo":
            print("Using MongoDB for add_item")
            print("Mongo URI:", os.environ.get("MONGO_URI"))
            conn.items.insert_one(
                {
                    "user_id": current_user.id,
                    "name": name,
                    "expiry_date": expiry_date,
                    "category": category,
                    "days_since_purchase": days_since_purchase,
                    "storage_temp": storage_temp,
                    "predicted_days": predicted_days,
                    "created_at": datetime.utcnow(),
                }
            )
        else:
            cursor = get_cursor(conn)
            cursor.execute(
                sql_query(
                    "INSERT INTO items (user_id, name, expiry_date, category, days_since_purchase, storage_temp, predicted_days) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
                ),
                (
                    current_user.id,
                    name,
                    expiry_date,
                    category,
                    days_since_purchase,
                    storage_temp,
                    predicted_days,
                ),
            )
            conn.commit()
            cursor.close()
            conn.close()

        settings = load_user_settings(current_user.id)
        phone_number = normalize_phone(settings.get("phone")) or normalize_phone(os.environ.get("TWILIO_TO_NUMBER")) or normalize_phone("8792179081")
        expiry_status = "Expired" if date.fromisoformat(expiry_date) < date.today() else "Expiring Soon" if (date.fromisoformat(expiry_date) - date.today()).days <= settings.get("alert_days", 2) else "Fresh"
        message = (
            f"New item added: {name}. Expiry date: {expiry_date}. "
            f"Status: {expiry_status}. Risk: {get_ml_status(predicted_days)}."
        )

        if settings.get("sms_enabled") and phone_number:
            sent = send_sms(message, to_number=phone_number)
            if sent:
                flash("Food item added successfully and SMS sent.")
            else:
                flash("Food item added successfully, but SMS could not be sent. Check Twilio settings.")
        elif settings.get("sms_enabled"):
            flash("Food item added successfully, but no phone number is configured for SMS.")
        else:
            flash("Food item added successfully.")
    except Exception as exc:
        print(f"Add item failed: {exc}")
        flash("Unable to add item. Check your database connection.")

    return redirect(url_for("dashboard", hide_alert_popup=1))


@app.route("/debug-db")
def debug_db():
    conn = get_db_connection()
    data = {"db_driver": DB_DRIVER}
    if DB_DRIVER == "mongo":
        data["items_count"] = conn.items.count_documents({})
        data["settings_count"] = conn.settings.count_documents({})
        data["users_count"] = conn.users.count_documents({})
    else:
        cursor = get_cursor(conn)
        cursor.execute(sql_query("SELECT COUNT(*) AS count FROM items"))
        data["items_count"] = cursor.fetchone()[0]
        cursor.execute(sql_query("SELECT COUNT(*) AS count FROM settings"))
        data["settings_count"] = cursor.fetchone()[0]
        cursor.execute(sql_query("SELECT COUNT(*) AS count FROM users"))
        data["users_count"] = cursor.fetchone()[0]
        cursor.close()
        conn.close()
    return data


@app.route("/delete/<item_id>")
@login_required
def delete_item(item_id):
    conn = get_db_connection()
    if DB_DRIVER == "mongo":
        conn.items.delete_one({"_id": mongo_object_id(item_id), "user_id": current_user.id})
    else:
        cursor = conn.cursor()
        cursor.execute(sql_query("DELETE FROM items WHERE id = %s AND user_id = %s"), (item_id, current_user.id))
        conn.commit()
        cursor.close()
        conn.close()
    flash("Item deleted successfully.")
    return redirect(url_for("dashboard", hide_alert_popup=1))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)), use_reloader=False)
