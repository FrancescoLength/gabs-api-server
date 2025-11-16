import sqlite3
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATABASE_FILE = 'auto_bookings.db'

def migrate():
    """
    Migrates the auto_bookings table to remove the 'notification_sent' column.
    """
    logging.info("Starting migration to remove 'notification_sent' column from 'auto_bookings' table.")
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    try:
        # --- Step 1: Check if the old table exists to prevent re-running ---
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_bookings_old'")
        if cursor.fetchone():
            logging.warning("Migration seems to have been run before ('auto_bookings_old' exists). Aborting.")
            return

        # --- Step 2: Backup data from the existing table ---
        logging.info("Backing up data from 'auto_bookings'...")
        cursor.execute("SELECT id, username, class_name, target_time, status, created_at, last_booked_date, last_attempt_at, day_of_week, instructor, retry_count FROM auto_bookings")
        backup_data = cursor.fetchall()
        logging.info(f"Backed up {len(backup_data)} rows.")

        # --- Step 3: Rename the old table ---
        logging.info("Renaming 'auto_bookings' to 'auto_bookings_old'...")
        cursor.execute("ALTER TABLE auto_bookings RENAME TO auto_bookings_old")

        # --- Step 4: Create the new table with the updated schema ---
        logging.info("Creating new 'auto_bookings' table without 'notification_sent' column...")
        cursor.execute('''
            CREATE TABLE auto_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                class_name TEXT NOT NULL,
                target_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                last_booked_date TEXT,
                last_attempt_at INTEGER,
                day_of_week TEXT,
                instructor TEXT,
                retry_count INTEGER DEFAULT 0
            )
        ''')

        # --- Step 5: Repopulate the new table with the backup data ---
        if backup_data:
            logging.info("Repopulating new 'auto_bookings' table...")
            cursor.executemany('''
                INSERT INTO auto_bookings (id, username, class_name, target_time, status, created_at, last_booked_date, last_attempt_at, day_of_week, instructor, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', backup_data)
            logging.info("Data repopulated successfully.")
        else:
            logging.info("No data to repopulate.")

        # --- Step 6: Verify data integrity ---
        cursor.execute("SELECT COUNT(*) FROM auto_bookings")
        new_row_count = cursor.fetchone()[0]
        if new_row_count == len(backup_data):
            logging.info(f"Verification successful: New table has {new_row_count} rows, matching the backup.")
            
            # --- Step 7: Drop the old table ---
            logging.info("Dropping old table 'auto_bookings_old'...")
            cursor.execute("DROP TABLE auto_bookings_old")
            
            conn.commit()
            logging.info("Migration completed successfully!")
        else:
            logging.error(f"Verification failed! New table has {new_row_count} rows, but backup had {len(backup_data)}. Rolling back.")
            conn.rollback()

    except Exception as e:
        logging.error(f"An error occurred during migration: {e}")
        logging.error("Rolling back changes.")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
