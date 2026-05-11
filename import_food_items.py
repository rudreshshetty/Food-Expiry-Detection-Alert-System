import os
import csv
from datetime import datetime
from pymongo import MongoClient
import numpy as np
import pickle

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017")
db = client["food_expiry"]

# Function to parse storage temp
def parse_storage_temp(temp_str):
    """Extract numeric value from storage temperature string like '8°C' or '-18°C'"""
    if not temp_str:
        return 25.0
    # Remove non-numeric characters except decimal point and negative sign
    temp_str = temp_str.strip()
    temp_value = ''.join(c for c in temp_str if c.isdigit() or c == '-' or c == '.')
    try:
        return float(temp_value) if temp_value else 25.0
    except:
        return 25.0

# Load ML model for prediction
model = None
model_path = os.path.join(os.path.dirname(__file__), "model.pkl")
try:
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    print("✓ ML model loaded successfully")
except Exception as e:
    print(f"✗ ML model not found, predictions will be skipped: {e}")

def predict_shelf_life(days_since_purchase, storage_temp):
    """Predict shelf life using ML model"""
    if model is None:
        return None
    try:
        X = np.array([[days_since_purchase, storage_temp]], dtype=float)
        predicted_days = model.predict(X)[0]
        return float(predicted_days)
    except Exception as e:
        print(f"  Prediction error: {e}")
        return None

# Read CSV file
csv_file_path = r"c:\Users\Rudresha B M\Downloads\random_food_items_50.csv"
user_id = "testuser"

print(f"\n📂 Reading CSV file: {csv_file_path}")
print(f"👤 Importing items for user: {user_id}\n")

items_added = 0
today = datetime.today().date()

try:
    with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        
        for row_num, row in enumerate(reader, start=2):  # Start from 2 because row 1 is header
            try:
                food_name = row.get('Food Name', '').strip()
                category = row.get('Category', 'Grocery').strip()
                expiry_date_str = row.get('Expiry Date', '').strip()
                storage_temp_str = row.get('Storage Temp', '25°C').strip()
                
                # Parse expiry date (DD-MM-YYYY format)
                try:
                    expiry_date_obj = datetime.strptime(expiry_date_str, "%d-%m-%Y")
                except:
                    print(f"  ✗ Row {row_num}: Invalid date format '{expiry_date_str}', skipping")
                    continue
                
                # Parse storage temperature
                storage_temp = parse_storage_temp(storage_temp_str)
                
                # Calculate days since purchase and days remaining
                days_since_purchase = (today - expiry_date_obj.date()).days
                days_remaining = (expiry_date_obj.date() - today).days
                
                # Make ML prediction
                predicted_days = predict_shelf_life(days_since_purchase, storage_temp)
                
                # Create item document (use datetime object for MongoDB)
                item = {
                    "user_id": user_id,
                    "name": food_name,
                    "category": category,
                    "expiry_date": expiry_date_obj,  # Use datetime object, not date
                    "days_since_purchase": days_since_purchase,
                    "storage_temp": storage_temp,
                    "predicted_days": predicted_days,
                    "created_at": datetime.now()
                }
                
                # Insert into MongoDB
                result = db.items.insert_one(item)
                items_added += 1
                
                status = "✓" if result.inserted_id else "✗"
                print(f"  {status} {food_name:20} | {category:15} | Expires: {expiry_date_str:12} | {storage_temp}°C")
                
            except Exception as e:
                print(f"  ✗ Row {row_num}: Error processing row - {e}")
                continue
    
    print(f"\n✅ Successfully imported {items_added} food items to database!")
    print(f"📊 Items stored in collection: food_expiry.items")
    print(f"👤 User: {user_id}")
    
except FileNotFoundError:
    print(f"✗ Error: File not found at {csv_file_path}")
except Exception as e:
    print(f"✗ Error importing CSV: {e}")
finally:
    client.close()
