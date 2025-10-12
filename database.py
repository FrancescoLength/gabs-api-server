import sqlite3
import os
from datetime import datetime

DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auto_bookings.db')

def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    # Create a new table without the single-booking fields
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            class_name TEXT NOT NULL,
            target_time TEXT NOT NULL, -- HH:MM
            status TEXT NOT NULL, -- e.g., 'pending', 'booked', 'failed'
            created_at INTEGER NOT NULL, -- Unix timestamp
            last_attempt_at INTEGER, -- Unix timestamp
            retry_count INTEGER DEFAULT 0,
            day_of_week TEXT NOT NULL, -- e.g., 'Monday', 'Tuesday'
            instructor TEXT NOT NULL, -- Instructor name
            last_booked_date TEXT, -- YYYY-MM-DD, last date a recurring booking was made for
            notification_sent INTEGER DEFAULT 0 -- 0 for not sent, 1 for sent
        )
    ''')

    conn.commit()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

def save_push_subscription(username, subscription_info):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    created_at = int(datetime.now().timestamp())
    endpoint = subscription_info['endpoint']
    p256dh = subscription_info['keys']['p256dh']
    auth = subscription_info['keys']['auth']

    # Check if subscription already exists for this endpoint
    cursor.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    existing_sub = cursor.fetchone()

    if existing_sub:
        # Update existing subscription
        cursor.execute("UPDATE push_subscriptions SET username = ?, p256dh = ?, auth = ?, created_at = ? WHERE endpoint = ?",
                       (username, p256dh, auth, created_at, endpoint))
    else:
        # Insert new subscription
        cursor.execute("INSERT INTO push_subscriptions (username, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
                       (username, endpoint, p256dh, auth, created_at))
    conn.commit()
    conn.close()

def get_push_subscriptions_for_user(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE username = ?", (username,))
    subscriptions = []
    for sub in cursor.fetchall():
        subscriptions.append({
            'endpoint': sub[0],
            'keys': {
                'p256dh': sub[1],
                'auth': sub[2]
            }
        })
    conn.close()
    return subscriptions

def delete_push_subscription(endpoint):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

def get_all_push_subscriptions():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, endpoint, created_at FROM push_subscriptions")
    subscriptions = []
    for sub in cursor.fetchall():
        subscriptions.append({
            'id': sub[0],
            'username': sub[1],
            'endpoint': sub[2],
            'created_at': sub[3]
        })
    conn.close()
    return subscriptions

def add_auto_booking(username, class_name, target_time, day_of_week, instructor):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    created_at = int(datetime.now().timestamp())
    cursor.execute("INSERT INTO auto_bookings (username, class_name, target_time, status, created_at, day_of_week, instructor, notification_sent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (username, class_name, target_time, 'pending', created_at, day_of_week, instructor, 0))
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    return booking_id

def get_pending_auto_bookings():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent FROM auto_bookings WHERE status = 'pending'")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def update_auto_booking_status(booking_id, status, last_booked_date=None, last_attempt_at=None, retry_count=None, notification_sent=None):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    update_sql = "UPDATE auto_bookings SET status = ?"
    params = [status]
    if last_booked_date is not None:
        update_sql += ", last_booked_date = ?"
        params.append(last_booked_date)
    if last_attempt_at is not None:
        update_sql += ", last_attempt_at = ?"
        params.append(last_attempt_at)
    if retry_count is not None:
        update_sql += ", retry_count = ?"
        params.append(retry_count)
    if notification_sent is not None:
        update_sql += ", notification_sent = ?"
        params.append(notification_sent)
    params.append(booking_id)
    update_sql += " WHERE id = ?"
    cursor.execute(update_sql, params)
    conn.commit()
    conn.close()

def get_auto_bookings_for_user(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent FROM auto_bookings WHERE username = ?", (username,))
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def get_upcoming_bookings_for_notification():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    # Select bookings that are pending, notification not sent, and within the notification window
    # For recurring bookings, we need to calculate the next occurrence date
    # This function will return all pending bookings that haven't sent a notification yet.
    # The time-based filtering will happen in the APScheduler job.
    cursor.execute("SELECT id, username, class_name, target_time, day_of_week, instructor, last_booked_date FROM auto_bookings WHERE status = 'pending' AND notification_sent = 0")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def cancel_auto_booking(booking_id, username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_bookings WHERE id = ? AND username = ?", (booking_id, username))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

def get_all_auto_bookings():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent FROM auto_bookings")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

if __name__ == '__main__':
    init_db()
    print("Database initialized and table 'auto_bookings' updated.")