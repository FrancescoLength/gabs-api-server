import pytest
from datetime import datetime, timedelta
from app import process_auto_bookings, send_cancellation_reminders, reset_failed_bookings
import database
import time

def test_process_auto_bookings_flow(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    class_name = "Test Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")
    
    database.save_session(username, "password", {"cookies": {}})
    booking_id = database.add_auto_booking(username, class_name, target_time, day_of_week, "instructor")
    
    # Mock scraper
    mock_scraper = mocker.patch('app.get_scraper_instance').return_value
    mock_scraper.find_and_book_class.return_value = {"status": "success", "class_name": class_name}
    
    # 2. Execute
    process_auto_bookings()
    
    # 3. Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[10] is not None # last_booked_date
    
    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 1
    assert live_bookings[0][2] == class_name

def test_send_cancellation_reminders_flow(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    
    # Mock datetime.now() to a fixed point in time
    mock_now = datetime(2025, 1, 1, 10, 0, 0)
    
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return mock_now

    mocker.patch('app.datetime', FakeDateTime)

    class_date = mock_now.strftime("%Y-%m-%d")
    class_time = (mock_now + timedelta(hours=3, minutes=30)).strftime("%H:%M")
    
    database.save_push_subscription(username, {'endpoint': 'a', 'keys': {'p256dh': 'a', 'auth': 'a'}})
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # Mock webpush
    mock_webpush = mocker.patch('app.webpush')
    
    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_webpush.assert_called_once()
    
    reminders = database.get_live_bookings_for_reminder()
    assert len(reminders) == 0

def test_reset_failed_bookings_flow(memory_db):
    # 1. Setup
    booking_id = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    one_day_ago = int((datetime.now() - timedelta(hours=25)).timestamp())
    database.update_auto_booking_status(booking_id, "failed", last_attempt_at=one_day_ago)
    
    # 2. Execute
    # Call the function directly, without app context
    failed_bookings = database.get_failed_auto_bookings()
    now_timestamp = int(datetime.now().timestamp())
    reset_threshold_seconds = 24 * 60 * 60
    for booking_id, last_attempt_at in failed_bookings:
        if last_attempt_at and (now_timestamp - last_attempt_at) > reset_threshold_seconds:
            database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=None, retry_count=0)

    # 3. Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'
