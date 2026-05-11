import os
import csv
from datetime import datetime
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017")
db = client["food_expiry"]

# Create a default user if not exists
username = "admin"
if not db.users.find_one({"username": username}):
    db.users.insert_one({"username": username, "password": generate_password_hash("password")})
    db.settings.insert_one({
        "user_id": username,
        "phone": "",
        "sms_enabled": False,
        "expired_alert": True,
        "expiring_alert": True,
        "ml_alert": True,
        "alert_days": 2,
        "storage_mode": "Fridge",
        "appearance": "Light",
    })

# Function to get storage temp
def get_storage_temp(category):
    category = (category or "").strip().title()
    if category == "Dairy":
        return 5.0
    if category == "Beverages":
        return 10.0
    return 25.0

# Load model for prediction
import pickle
model_path = os.path.join(os.path.dirname(__file__), "model.pkl")
try:
    with open(model_path, "rb") as f:
        model = pickle.load(f)
except:
    model = None

def predict_shelf_life(days_since_purchase, storage_temp):
    if model is None:
        return None
    try:
        import numpy as np
        X = np.array([[days_since_purchase, storage_temp]], dtype=float)
        predicted = float(model.predict(X)[0])
        return max(predicted, 0.0)
    except:
        return None

# Read CSV and insert items
csv_path = r"c:\Users\Admin\Downloads\inventory_data.csv"
with open(csv_path, 'r') as file:
    reader = csv.DictReader(file)
    for row in reader:
        name = row['Item']
        expiry_date = row['Expiry_Date']
        category = row['Category'].strip().title()
        days_since_purchase = 0  # Assume 0
        storage_temp = get_storage_temp(category)
        predicted_days = predict_shelf_life(days_since_purchase, storage_temp)

        item = {
            "user_id": username,
            "name": name,
            "expiry_date": expiry_date,
            "category": category,
            "days_since_purchase": days_since_purchase,
            "storage_temp": storage_temp,
            "predicted_days": predicted_days,
            "created_at": datetime.utcnow(),
        }
        db.items.insert_one(item)
        print(f"Inserted: {name}")

print("Import completed.")