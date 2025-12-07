import os
import sqlite3
import json
from datetime import datetime
import logging

DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auto_bookings.db')

def get_db_connection(timeout=30):
    """Establishes a database connection with a default timeout."""
    conn = sqlite3.connect(DATABASE_FILE, timeout=timeout)
    # Optional: If you want to fetch rows as dictionaries
    # conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Enable Write-Ahead Logging (WAL) for better concurrency
    cursor.execute('PRAGMA journal_mode=WAL;')
    
    # Auto-booking table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auto_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            class_name TEXT NOT NULL,
            target_time TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_attempt_at INTEGER,
            retry_count INTEGER DEFAULT 0,
            day_of_week TEXT NOT NULL,
            instructor TEXT,
            last_booked_date TEXT,
            notification_sent INTEGER DEFAULT 0
        )
    ''')
    
    # Live bookings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            class_name TEXT NOT NULL,
            class_date TEXT NOT NULL,
            class_time TEXT NOT NULL,
            instructor TEXT,
            reminder_sent INTEGER DEFAULT 0,
            created_at TEXT,
            auto_booking_id INTEGER,
            FOREIGN KEY (auto_booking_id) REFERENCES auto_bookings (id)
        )
    ''')

    # Push subscriptions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    ''')

    # Sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            username TEXT PRIMARY KEY,
            encrypted_password TEXT NOT NULL,
            session_data TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

# Auto-booking functions
def add_auto_booking(username, class_name, target_time, day_of_week, instructor):
    conn = get_db_connection()
    cursor = conn.cursor()
    created_at = int(datetime.now().timestamp())
    cursor.execute("INSERT INTO auto_bookings (username, class_name, target_time, status, created_at, day_of_week, instructor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (username, class_name, target_time, 'pending', created_at, day_of_week, instructor))
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    return booking_id

def get_pending_auto_bookings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM auto_bookings WHERE status = 'pending'")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def update_auto_booking_status(booking_id, status=None, last_booked_date=None, last_attempt_at=None, retry_count=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if last_booked_date is not None:
        updates.append("last_booked_date = ?")
        params.append(last_booked_date)
    if last_attempt_at is not None:
        updates.append("last_attempt_at = ?")
        params.append(last_attempt_at)
    if retry_count is not None:
        updates.append("retry_count = ?")
        params.append(retry_count)

    if not updates:
        return

    query = f"UPDATE auto_bookings SET {', '.join(updates)} WHERE id = ?"
    params.append(booking_id)
    
    cursor.execute(query, tuple(params))
    conn.commit()
    conn.close()

def get_auto_bookings_for_user(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM auto_bookings WHERE username = ?", (username,))
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def cancel_auto_booking(booking_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_bookings WHERE id = ? AND username = ?", (booking_id, username))
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    return deleted_rows > 0

def get_stuck_bookings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, last_attempt_at, status FROM auto_bookings WHERE status IN ('failed', 'in_progress')")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def get_auto_booking_by_id(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date FROM auto_bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()
    conn.close()
    return booking

def lock_auto_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN EXCLUSIVE")
        cursor.execute("SELECT status FROM auto_bookings WHERE id = ?", (booking_id,))
        result = cursor.fetchone()
        if result and result[0] == 'pending':
            cursor.execute("UPDATE auto_bookings SET status = 'in_progress' WHERE id = ?", (booking_id,))
            conn.commit()
            return True
        conn.commit() # or rollback
        return False
    except sqlite3.OperationalError as e:
        logging.error(f"Database lock error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_all_auto_bookings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date FROM auto_bookings")
    bookings = []
    for row in cursor.fetchall():
        bookings.append({
            "id": row[0],
            "username": row[1],
            "class_name": row[2],
            "target_time": row[3],
            "status": row[4],
            "created_at": row[5],
            "last_attempt_at": row[6],
            "retry_count": row[7],
            "day_of_week": row[8],
            "instructor": row[9],
            "last_booked_date": row[10]
        })
    conn.close()
    return bookings

# Live booking functions
def add_live_booking(username, class_name, class_date, class_time, instructor=None, auto_booking_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    created_at = datetime.now().strftime('%d/%m/%y %H:%M:%S')
    cursor.execute("INSERT INTO live_bookings (username, class_name, class_date, class_time, instructor, reminder_sent, created_at, auto_booking_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (username, class_name, class_date, class_time, instructor, 0, created_at, auto_booking_id))
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    return booking_id

def get_live_bookings_for_user(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM live_bookings WHERE username = ?", (username,))
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def live_booking_exists(username, class_name, class_date, class_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM live_bookings WHERE username = ? AND class_name = ? AND class_date = ? AND class_time = ?",
                   (username, class_name, class_date, class_time))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def delete_live_booking(username, class_name, class_date, class_time):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM live_bookings WHERE username = ? AND class_name = ? AND class_date = ? AND class_time = ?",
                   (username, class_name, class_date, class_time))
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    return deleted_rows > 0

def get_live_bookings_for_reminder():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, class_date, class_time, instructor FROM live_bookings WHERE reminder_sent = 0")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def update_live_booking_reminder_status(booking_id, reminder_sent):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE live_bookings SET reminder_sent = ? WHERE id = ?", (reminder_sent, booking_id))
    conn.commit()
    conn.close()

def update_live_booking_name(booking_id, new_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE live_bookings SET class_name = ? WHERE id = ?", (new_name, booking_id))
    conn.commit()
    conn.close()

def get_all_live_bookings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, class_date, class_time, instructor, reminder_sent, created_at, auto_booking_id FROM live_bookings")
    bookings = []
    for row in cursor.fetchall():
        bookings.append({
            "id": row[0],
            "username": row[1],
            "class_name": row[2],
            "class_date": row[3],
            "class_time": row[4],
            "instructor": row[5],
            "reminder_sent": row[6],
            "created_at": row[7],
            "auto_booking_id": row[8]
        })
    conn.close()
    return bookings

# Push subscription functions
def save_push_subscription(username, subscription_info):
    conn = get_db_connection()
    cursor = conn.cursor()
    endpoint = subscription_info.get('endpoint')
    p256dh = subscription_info.get('keys', {}).get('p256dh')
    auth = subscription_info.get('keys', {}).get('auth')
    created_at = int(datetime.now().timestamp()) # Add created_at
    cursor.execute("INSERT OR REPLACE INTO push_subscriptions (username, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
                   (username, endpoint, p256dh, auth, created_at))
    conn.commit()
    conn.close()

def get_push_subscriptions_for_user(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE username = ?", (username,))
    subscriptions = []
    for row in cursor.fetchall():
        subscriptions.append({
            "endpoint": row[0],
            "keys": {
                "p256dh": row[1],
                "auth": row[2]
            }
        })
    conn.close()
    return subscriptions

def delete_push_subscription(endpoint):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    return deleted_rows > 0

def get_all_push_subscriptions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, endpoint, created_at FROM push_subscriptions")
    subscriptions = []
    for row in cursor.fetchall():
        subscriptions.append({
            "id": row[0],
            "username": row[1],
            "endpoint": row[2],
            "created_at": row[3]
        })
    conn.close()
    return subscriptions

# Session functions
def save_session(username, encrypted_password, session_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    updated_at = int(datetime.now().timestamp())
    cursor.execute("INSERT OR REPLACE INTO sessions (username, encrypted_password, session_data, updated_at) VALUES (?, ?, ?, ?)",
                   (username, encrypted_password, json.dumps(session_data), updated_at))
    conn.commit()
    conn.close()

def touch_session(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    updated_at = int(datetime.now().timestamp())
    cursor.execute("UPDATE sessions SET updated_at = ? WHERE username = ?", (updated_at, username))
    conn.commit()
    conn.close()

def load_session(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT encrypted_password, session_data FROM sessions WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1])
    return None, None

def delete_session(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    deleted_rows = cursor.rowcount
    conn.close()
    return deleted_rows > 0

def get_all_sessions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, encrypted_password, session_data, updated_at FROM sessions")
    sessions = [{'username': row[0], 'encrypted_password': row[1], 'session_data': json.loads(row[2]), 'updated_at': row[3]} for row in cursor.fetchall()]
    conn.close()
    return sessions

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM sessions")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users