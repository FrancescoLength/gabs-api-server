import pytest
import sqlite3
from datetime import datetime
import database

@pytest.fixture
def memory_db(monkeypatch):
    db_uri = "file::memory:?cache=shared"
    monkeypatch.setattr(database, "DATABASE_FILE", db_uri)
    conn = sqlite3.connect(db_uri, uri=True)
    database.init_db()
    yield conn
    conn.close()

def test_add_auto_booking(memory_db):
    username = "test_user"
    class_name = "Test Class"
    target_time = "10:00"
    day_of_week = "Monday"
    instructor = "Test Instructor"

    booking_id = database.add_auto_booking(username, class_name, target_time, day_of_week, instructor)

    cursor = memory_db.cursor()
    cursor.execute("SELECT * FROM auto_bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()

    assert booking is not None
    assert booking[1] == username
    assert booking[2] == class_name
    assert booking[3] == target_time
    assert booking[4] == 'pending'
    assert booking[8] == day_of_week
    assert booking[9] == instructor

def test_get_pending_auto_bookings(memory_db):
    database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    cursor = memory_db.cursor()
    cursor.execute("INSERT INTO auto_bookings (username, class_name, target_time, status, created_at, day_of_week, instructor, notification_sent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("test_user", "Test Class 2", "12:00", "failed", int(datetime.now().timestamp()), "Tuesday", "Test Instructor 2", 0))
    memory_db.commit()

    pending_bookings = database.get_pending_auto_bookings()

    assert len(pending_bookings) == 1
    assert pending_bookings[0][2] == "Test Class"

def test_update_auto_booking_status(memory_db):
    booking_id = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")

    database.update_auto_booking_status(booking_id, "booked", last_booked_date="2025-10-26")

    cursor = memory_db.cursor()
    cursor.execute("SELECT status, last_booked_date FROM auto_bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()

    assert booking[0] == "booked"
    assert booking[1] == "2025-10-26"

def test_get_auto_bookings_for_user(memory_db):
    database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    database.add_auto_booking("test_user", "Test Class 2", "12:00", "Tuesday", "Test Instructor 2")
    database.add_auto_booking("another_user", "Test Class 3", "14:00", "Wednesday", "Test Instructor 3")

    bookings = database.get_auto_bookings_for_user("test_user")

    assert len(bookings) == 2

def test_cancel_auto_booking(memory_db):
    booking_id = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")

    result = database.cancel_auto_booking(booking_id, "test_user")

    assert result is True

    cursor = memory_db.cursor()
    cursor.execute("SELECT * FROM auto_bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()

    assert booking is None

def test_save_and_get_push_subscription(memory_db):
    username = "test_user"
    subscription_info = {
        'endpoint': 'test_endpoint',
        'keys': {
            'p256dh': 'test_p256dh',
            'auth': 'test_auth'
        }
    }

    database.save_push_subscription(username, subscription_info)

    subscriptions = database.get_push_subscriptions_for_user(username)

    assert len(subscriptions) == 1
    assert subscriptions[0]['endpoint'] == 'test_endpoint'

def test_delete_push_subscription(memory_db):
    username = "test_user"
    subscription_info = {
        'endpoint': 'test_endpoint',
        'keys': {
            'p256dh': 'test_p256dh',
            'auth': 'test_auth'
        }
    }
    database.save_push_subscription(username, subscription_info)

    result = database.delete_push_subscription('test_endpoint')

    assert result is True

    subscriptions = database.get_push_subscriptions_for_user(username)
    assert len(subscriptions) == 0

def test_get_failed_auto_bookings(memory_db):
    database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    booking_id = database.add_auto_booking("test_user", "Test Class 2", "12:00", "Tuesday", "Test Instructor 2")
    database.update_auto_booking_status(booking_id, "failed")

    failed_bookings = database.get_failed_auto_bookings()

    assert len(failed_bookings) == 1
    assert failed_bookings[0][0] == booking_id
