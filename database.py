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
            last_booked_date TEXT -- YYYY-MM-DD, last date a recurring booking was made for
        )
    ''')

    conn.commit()
    conn.close()

def add_auto_booking(username, class_name, target_time, day_of_week, instructor):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    created_at = int(datetime.now().timestamp())
    cursor.execute("INSERT INTO auto_bookings (username, class_name, target_time, status, created_at, day_of_week, instructor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (username, class_name, target_time, 'pending', created_at, day_of_week, instructor))
    conn.commit()
    booking_id = cursor.lastrowid
    conn.close()
    return booking_id

def get_pending_auto_bookings():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date FROM auto_bookings WHERE status = 'pending'")
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def update_auto_booking_status(booking_id, status, last_booked_date=None, last_attempt_at=None, retry_count=None):
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
    params.append(booking_id)
    update_sql += " WHERE id = ?"
    cursor.execute(update_sql, params)
    conn.commit()
    conn.close()

def get_auto_bookings_for_user(username):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date FROM auto_bookings WHERE username = ?", (username,))
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

if __name__ == '__main__':
    init_db()
    print("Database initialized and table 'auto_bookings' updated.")