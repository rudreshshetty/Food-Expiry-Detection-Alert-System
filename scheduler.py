import os
import pickle
import sqlite3
import time
from datetime import date, datetime, timedelta

import numpy as np
try:
    import mysql.connector
except ImportError:
    mysql = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from pymongo import MongoClient
    from bson.objectid import ObjectId
except ImportError:
    MongoClient = None
    ObjectId = None
import schedule

DB_DRIVER = None

if load_dotenv is not None:
    load_dotenv()

try:
    from twilio.rest import Client
except ImportError:
    Client = None


def get_db_connection():
    global DB_DRIVER
    mongo_uri = os.environ.get("MONGO_URI")
    mongo_db_name = os.environ.get("MONGO_DB", "food_expiry")
    db_host = os.environ.get("DB_HOST")
    db_user = os.environ.get("DB_USER")
    db_password = os.environ.get("DB_PASSWORD", "")
    db_name = os.environ.get("DB_NAME", "food_expiry")

    if mongo_uri and MongoClient is not None:
        try:
            client = MongoClient(mongo_uri)
            db = client[mongo_db_name]
            DB_DRIVER = "mongo"
            return db
        except Exception:
            pass

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


def sql_query(query_string):
    if DB_DRIVER == "sqlite":
        return query_string.replace("%s", "?")
    return query_string


def get_cursor(conn):
    if DB_DRIVER == "mysql":
        return conn.cursor(dictionary=True)
    return conn.cursor()


def load_ml_model():
    model_path = os.path.join(os.path.dirname(__file__), "model.pkl")
    try:
        with open(model_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


model = load_ml_model()


def predict_shelf_life(days_since_purchase, storage_temp):
    if model is None:
        return None
    try:
        X = np.array([[days_since_purchase, storage_temp]], dtype=float)
        return max(float(model.predict(X)[0]), 0.0)
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


def normalize_phone(phone):
    if not phone:
        return None
    phone = str(phone).strip()
    if phone.startswith("+"):
        return phone
    if phone.isdigit() and len(phone) == 10:
        return "+91" + phone
    return phone


def send_sms(body, to_number=None):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    default_number = normalize_phone(os.environ.get("TWILIO_TO_NUMBER") or "8792179081")
    to_number = normalize_phone(to_number) if to_number else default_number

    if not all([account_sid, auth_token, from_number, to_number, Client]):
        print("Twilio is not configured or not installed. SMS skipped.")
        return

    client = Client(account_sid, auth_token)
    message = client.messages.create(body=body, from_=from_number, to=to_number)
    print(f"SMS sent to {to_number}: {message.sid}")


def build_alert_message(items):
    parts = []
    for item in items:
        label = item.get("ml_status") or item.get("status")
        parts.append(
            f"{item['name']} ({label}) expires in {item['days_left']} day(s)"
        )
    return "Expiry Alert: " + "; ".join(parts)


def check_expiry():
    conn = get_db_connection()
    rows = []
    if DB_DRIVER == "mongo":
        cursor = conn.items.find({}, {"name": 1, "expiry_date": 1, "days_since_purchase": 1, "storage_temp": 1})
        rows = list(cursor)
    else:
        cursor = get_cursor(conn)
        cursor.execute(sql_query(
            "SELECT name, expiry_date, days_since_purchase, storage_temp FROM items"
        ))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

    alert_items = []
    for item in rows:
        expiry_value = item["expiry_date"]
        if isinstance(expiry_value, str):
            expiry_value = date.fromisoformat(expiry_value)
        days_left = (expiry_value - date.today()).days
        status = "Expired" if days_left < 0 else "Expiring Soon" if days_left <= 2 else "Fresh"

        predicted_days = None
        if item.get("days_since_purchase") is not None:
            predicted_days = predict_shelf_life(
                item.get("days_since_purchase", 0), item.get("storage_temp", 25.0)
            )

        ml_status = get_ml_status(predicted_days)
        if status != "Fresh" or ml_status in ("High Risk", "Consume Soon"):
            alert_items.append(
                {
                    "name": item["name"],
                    "days_left": days_left,
                    "status": status,
                    "predicted_days": predicted_days,
                    "ml_status": ml_status,
                }
            )

    recipients = set()
    if DB_DRIVER == "mongo":
        settings_cursor = conn.settings.find({"sms_enabled": True, "phone": {"$ne": ""}})
        for setting in settings_cursor:
            phone = normalize_phone(setting.get("phone"))
            if phone:
                recipients.add(phone)
    else:
        cursor = get_cursor(conn)
        cursor.execute(sql_query("SELECT phone FROM settings WHERE sms_enabled = %s AND phone != ''"), (1,))
        for row in cursor.fetchall():
            phone = normalize_phone(row[0])
            if phone:
                recipients.add(phone)
        cursor.close()
        conn.close()

    if not recipients:
        recipients.add(normalize_phone(os.environ.get("TWILIO_TO_NUMBER") or "8792179081"))

    if alert_items:
        print("Found expiring or at-risk items:")
        for item in alert_items:
            print(
                f"- {item['name']}: {item['status']} / {item['ml_status']} / {item['days_left']} days"
            )
        for recipient in recipients:
            send_sms(build_alert_message(alert_items), to_number=recipient)
    else:
        print("No expiring items found today.")


if __name__ == "__main__":
    check_expiry()
    schedule.every().day.at("09:00").do(check_expiry)
    print("Scheduler started. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)
