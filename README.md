# Food Expiry Detection & Alert System

A full-stack Flask app to manage food items, predict shelf-life with an ML model, visualize expiry trends, and send alerts.

## Features

- User registration and login
- Dashboard for food items
- Add/delete items
- Date-based expiry status
- ML-based shelf-life prediction
- Popup alerts for expiring or high-risk items
- Charts for status and prediction insights
- Optional Twilio SMS alerts
- Daily scheduler automation

## Setup

1. Create the MySQL database and tables:

```sql
CREATE DATABASE food_expiry;
USE food_expiry;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL
);

CREATE TABLE items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    expiry_date DATE NOT NULL,
    days_since_purchase INT DEFAULT 0,
    storage_temp FLOAT DEFAULT 25.0,
    predicted_days FLOAT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Train the ML model:

```bash
python train_model.py
```

4. Configure environment variables:

- `DB_HOST`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `FLASK_SECRET_KEY`

Optionally use MongoDB instead of MySQL/SQLite by setting:

- `MONGO_URI`
- `MONGO_DB` (default: `food_expiry`)
- `FLASK_SECRET_KEY`

Optional Twilio variables:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`
- `TWILIO_TO_NUMBER` (optional, defaults to `+918792179081`)

If you want immediate SMS alerts for newly added items, enable SMS in the app settings and keep `TWILIO_TO_NUMBER` set to `+918792179081` or leave it unset.


5. Run the app:

```bash
python app.py
```

6. Start the scheduler:

```bash
python scheduler.py
```

## Project Structure

- `app.py` — Flask app, ML prediction, dashboard logic
- `scheduler.py` — daily expiry check and SMS alerts
- `train_model.py` — model training script
- `model.pkl` — trained shelf-life model
- `dataset.csv` — training dataset
- `templates/` — HTML pages
- `static/style.css` — UI styles

## How it works

- When users add a food item, the app predicts remaining shelf life using the ML model.
- Dashboard displays expiry status and risk categories.
- Chart.js visualizes fresh/expiring/expired counts and ML prediction trends.
- Scheduler can send Twilio alerts for expiring or high-risk items.
