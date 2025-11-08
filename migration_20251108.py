import sqlite3
import os

DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auto_bookings.db')

def migrate_database():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    try:
        # 1. Create a new table with the desired schema
        cursor.execute('''
            CREATE TABLE auto_bookings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                class_name TEXT NOT NULL,
                target_time TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_attempt_at INTEGER,
                retry_count INTEGER DEFAULT 0,
                day_of_week TEXT NOT NULL,
                instructor TEXT NOT NULL,
                last_booked_date TEXT,
                notification_sent INTEGER DEFAULT 0
            )
        ''')

        # 2. Copy the data from the old table to the new table
        cursor.execute('''
            INSERT INTO auto_bookings_new (id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent)
            SELECT id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent
            FROM auto_bookings
        ''')

        # 3. Drop the old table
        cursor.execute('DROP TABLE auto_bookings')

        # 4. Rename the new table to the original name
        cursor.execute('ALTER TABLE auto_bookings_new RENAME TO auto_bookings')

        conn.commit()
        print("Database migration successful!")

    except Exception as e:
        conn.rollback()
        print(f"An error occurred during database migration: {e}")

    finally:
        conn.close()

if __name__ == '__main__':
    migrate_database()
