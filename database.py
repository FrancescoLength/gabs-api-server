import sqlite3
import os
from datetime import datetime, timedelta
import json

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
            notification_sent INTEGER DEFAULT 0 -- 0 for not sent, 1 for sent (for auto-booking reminders)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            class_name TEXT NOT NULL,
            class_date TEXT NOT NULL, -- YYYY-MM-DD
            class_time TEXT NOT NULL, -- HH:MM
            instructor TEXT,
            reminder_sent INTEGER DEFAULT 0, -- 0 for not sent, 1 for sent
            created_at TEXT NOT NULL, -- DD/MM/YY HH:MM:SS
            auto_booking_id INTEGER, -- Optional: Link to auto_bookings if originated from there
            FOREIGN KEY (auto_booking_id) REFERENCES auto_bookings(id)
        )
    ''')

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            username TEXT PRIMARY KEY,
            encrypted_password TEXT NOT NULL,
            session_data TEXT,
            updated_at INTEGER NOT NULL
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
        conn.commit()
        conn.close()
        return False
    else:
        # Insert new subscription
        cursor.execute("INSERT INTO push_subscriptions (username, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
                       (username, endpoint, p256dh, auth, created_at))
        conn.commit()
        conn.close()
        return True

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

def lock_auto_booking(booking_id):
    """
    Atomically sets a booking's status to 'in_progress' to prevent other threads from processing it.
    Returns True if the lock was acquired, False otherwise.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE auto_bookings SET status = 'in_progress' WHERE id = ? AND status = 'pending'", (booking_id,))
    conn.commit()
    # Use cursor.rowcount to check if a row was actually updated.
    # This is the atomic part of the lock.
    lock_acquired = cursor.rowcount > 0
    conn.close()
    return lock_acquired

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

def get_auto_booking_by_id(booking_id):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent FROM auto_bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()
    conn.close()
    return booking

def get_upcoming_bookings_for_notification():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    # Select bookings that are pending, notification not sent, and within the notification window
    # For recurring bookings, we need to calculate the next occurrence date
    # This function will return all pending bookings that haven't sent a notification yet.
    # The time-based filtering will happen in the APScheduler job.
    cursor.execute("SELECT id, username, class_name, target_time, day_of_week, instructor, last_booked_date, notification_sent FROM auto_bookings WHERE status = 'pending' AND notification_sent = 0")
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

def get_all_live_bookings():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, class_date, class_time, instructor, reminder_sent, created_at, auto_booking_id FROM live_bookings ORDER BY id DESC")
    bookings = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return bookings

def add_live_booking(username, class_name, class_date, class_time, instructor=None, auto_booking_id=None):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    created_at = datetime.now().strftime('%d/%m/%y %H:%M:%S')
    cursor.execute("INSERT INTO live_bookings (username, class_name, class_date, class_time, instructor, reminder_sent, created_at, auto_booking_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (username, class_name, class_date, class_time, instructor, 0, created_at, auto_booking_id))
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    return booking_id

def get_live_bookings_for_user(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM live_bookings WHERE username = ?", (username,))
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def get_live_bookings_for_reminder():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    # Select live bookings for which a reminder has not been sent yet
    cursor.execute("SELECT id, username, class_name, class_date, class_time, instructor FROM live_bookings WHERE reminder_sent = 0")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def update_live_booking_reminder_status(booking_id, reminder_sent=1):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE live_bookings SET reminder_sent = ? WHERE id = ?", (reminder_sent, booking_id))
    conn.commit()
    conn.close()

def live_booking_exists(username, class_name, class_date, class_time):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM live_bookings WHERE username = ? AND class_name = ? AND class_date = ? AND class_time = ?",
                   (username, class_name, class_date, class_time))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def delete_live_booking(username, class_name, class_date, class_time):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM live_bookings WHERE username = ? AND class_name = ? AND class_date = ? AND class_time = ?",
                   (username, class_name, class_date, class_time))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

def get_failed_auto_bookings():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, last_attempt_at FROM auto_bookings WHERE status = 'failed'")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def save_session(username, encrypted_password, session_data):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    updated_at = int(datetime.now().timestamp())
    session_data_json = json.dumps(session_data)
    
    cursor.execute("INSERT OR REPLACE INTO sessions (username, encrypted_password, session_data, updated_at) VALUES (?, ?, ?, ?)",
                   (username, encrypted_password, session_data_json, updated_at))
    conn.commit()
    conn.close()

def load_session(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT encrypted_password, session_data FROM sessions WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row:
        encrypted_password, session_data_json = row
        session_data = json.loads(session_data_json) if session_data_json else None
        return encrypted_password, session_data
    return None, None

def delete_session(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

def get_all_users():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT username FROM sessions")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def get_all_sessions():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT username, encrypted_password, session_data, updated_at FROM sessions")
    sessions = []
    for row in cursor.fetchall():
        sessions.append({
            "username": row[0],
            "encrypted_password": row[1],
            "session_data": json.loads(row[2]) if row[2] else None,
            "updated_at": row[3]
        })
    conn.close()
    return sessions

if __name__ == '__main__':
    init_db()
    print("Database initialized and tables created/updated.")