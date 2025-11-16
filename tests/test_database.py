import pytest
import sqlite3
from datetime import datetime
import database
import threading



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
    cursor.execute("INSERT INTO auto_bookings (username, class_name, target_time, status, created_at, day_of_week, instructor) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("test_user", "Test Class 2", "12:00", "failed", int(datetime.now().timestamp()), "Tuesday", "Test Instructor 2"))
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

def test_save_and_load_session(memory_db):
    username = "test_user"
    encrypted_password = "test_password"
    session_data = {"cookies": {"key": "value"}}
    database.save_session(username, encrypted_password, session_data)

    loaded_password, loaded_session_data = database.load_session(username)

    assert loaded_password == encrypted_password
    assert loaded_session_data == session_data

def test_load_non_existent_session(memory_db):
    loaded_password, loaded_session_data = database.load_session("non_existent_user")
    assert loaded_password is None
    assert loaded_session_data is None

def test_delete_session(memory_db):
    username = "test_user"
    database.save_session(username, "password", {"key": "value"})
    
    result = database.delete_session(username)
    assert result is True

    loaded_password, loaded_session_data = database.load_session(username)
    assert loaded_password is None
    assert loaded_session_data is None

def test_get_all_users(memory_db):
    database.save_session("user1", "pass1", {})
    database.save_session("user2", "pass2", {})
    database.save_session("user1", "pass1_updated", {}) # Test uniqueness

    users = database.get_all_users()

    assert len(users) == 2
    assert "user1" in users
    assert "user2" in users

def test_get_all_sessions(memory_db):
    database.save_session("user1", "pass1", {"c": 1})
    database.save_session("user2", "pass2", {"c": 2})

    sessions = database.get_all_sessions()

    assert len(sessions) == 2
    assert sessions[0]['username'] == 'user1'
    assert sessions[1]['username'] == 'user2'

def test_add_and_get_live_booking(memory_db):
    username = "test_user"
    class_name = "Test Live Class"
    class_date = "2025-12-25"
    class_time = "12:00"
    instructor = "Santa"
    
    booking_id = database.add_live_booking(username, class_name, class_date, class_time, instructor)
    
    bookings = database.get_live_bookings_for_user(username)
    
    assert len(bookings) == 1
    booking = bookings[0]
    assert booking[1] == username
    assert booking[2] == class_name
    assert booking[3] == class_date
    assert booking[4] == class_time
    assert booking[5] == instructor

def test_live_booking_exists(memory_db):
    username = "test_user"
    class_name = "Test Live Class"
    class_date = "2025-12-25"
    class_time = "12:00"
    
    database.add_live_booking(username, class_name, class_date, class_time)
    
    assert database.live_booking_exists(username, class_name, class_date, class_time) is True
    assert database.live_booking_exists(username, "Another Class", class_date, class_time) is False

def test_delete_live_booking(memory_db):
    username = "test_user"
    class_name = "Test Live Class"
    class_date = "2025-12-25"
    class_time = "12:00"
    
    database.add_live_booking(username, class_name, class_date, class_time)
    
    result = database.delete_live_booking(username, class_name, class_date, class_time)
    assert result is True
    
    assert database.live_booking_exists(username, class_name, class_date, class_time) is False

def test_get_live_bookings_for_reminder(memory_db):
    booking_id_1 = database.add_live_booking("user1", "Class 1", "2025-12-25", "10:00")
    database.update_live_booking_reminder_status(booking_id_1, reminder_sent=1)
    database.add_live_booking("user2", "Class 2", "2025-12-25", "11:00")
    database.add_live_booking("user1", "Class 3", "2025-12-25", "12:00")
    
    reminders = database.get_live_bookings_for_reminder()
    
    assert len(reminders) == 2
    assert reminders[0][2] == "Class 2"
    assert reminders[1][2] == "Class 3"

def test_update_live_booking_reminder_status(memory_db):
    booking_id = database.add_live_booking("user1", "Class 1", "2025-12-25", "10:00")
    
    database.update_live_booking_reminder_status(booking_id, reminder_sent=1)
    
    reminders = database.get_live_bookings_for_reminder()
    assert len(reminders) == 0

def test_lock_auto_booking(memory_db):
    booking_id = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    
    # First lock should succeed
    assert database.lock_auto_booking(booking_id) is True
    
    # Check status
    cursor = memory_db.cursor()
    cursor.execute("SELECT status FROM auto_bookings WHERE id = ?", (booking_id,))
    status = cursor.fetchone()[0]
    assert status == 'in_progress'
    
    # Second lock should fail
    assert database.lock_auto_booking(booking_id) is False

def test_lock_auto_booking_concurrency(memory_db):
    booking_id = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    
    results = []
    lock = threading.Lock()

    def worker():
        try:
            result = database.lock_auto_booking(booking_id)
            with lock:
                results.append(result)
        except sqlite3.OperationalError:
            with lock:
                results.append(False)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    # Only one thread should have acquired the lock
    assert results.count(True) == 1
    assert results.count(False) == 4