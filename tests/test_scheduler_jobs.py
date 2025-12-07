from datetime import datetime, timedelta
import pytest
import gabs_api_server # Import the package to fix NameError
# Import job functions and their dependencies from app
from gabs_api_server.app import (
    send_cancellation_reminders, reset_failed_bookings, refresh_sessions,
    app, debug_writer_queue, get_scraper_instance, handle_session_expiration
)
from gabs_api_server.services.auto_booking_service import process_auto_bookings_job
from gabs_api_server import database # Use alias to avoid conflict with mocker
from gabs_api_server import crypto
from gabs_api_server.scraper import SessionExpiredError # Import SessionExpiredError

def test_process_auto_bookings_flow(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    class_name = "Test Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")
    
    # Properly mock components that get_scraper_instance relies on
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")

    # Mock the scraper object and its method return value
    mock_scraper_obj = mocker.Mock()
    mock_scraper_obj.find_and_book_class.return_value = {
        "status": "success",
        "class_name": class_name,
        "message": "Booking successful",
        "action": "booking",
        "html_content": ""
    }
    
    # Define a lambda function to simulate get_scraper_instance_func returning the mock scraper
    get_mock_scraper_func = lambda username_arg: mock_scraper_obj

    booking_id = database.add_auto_booking(username, class_name, target_time, day_of_week, "instructor")
    
    # 2. Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=get_mock_scraper_func, # Pass the lambda function here
        handle_session_expiration_func=handle_session_expiration
    )
    
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

    mocker.patch('gabs_api_server.app.datetime', FakeDateTime) # Patch app's datetime
    mocker.patch('gabs_api_server.app.config.VAPID_PRIVATE_KEY', 'test_key') # Mock config values
    mocker.patch('gabs_api_server.app.config.VAPID_ADMIN_EMAIL', 'test@example.com') # Mock config values

    class_date = mock_now.strftime("%Y-%m-%d")
    class_time = (mock_now + timedelta(hours=3, minutes=30)).strftime("%H:%M")
    
    database.save_push_subscription(username, {'endpoint': 'a', 'keys': {'p256dh': 'a', 'auth': 'a'}})
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # Mock webpush
    mock_webpush = mocker.patch('gabs_api_server.app.webpush') # Patch app's webpush
    
    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_webpush.assert_called_once()
    
    reminders = database.get_live_bookings_for_reminder()
    assert len(reminders) == 0

def test_send_cancellation_reminders_outside_window(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    mock_now = datetime(2025, 1, 1, 10, 0, 0)
    
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return mock_now

    mocker.patch('gabs_api_server.app.datetime', FakeDateTime)
    mock_webpush = mocker.patch('gabs_api_server.app.webpush') # Store the mock object

    class_date = mock_now.strftime("%Y-%m-%d")
    # Class time is 5 hours away, so reminder should NOT be sent
    class_time = (mock_now + timedelta(hours=5)).strftime("%H:%M")
    
    database.save_push_subscription(username, {'endpoint': 'a', 'keys': {'p256dh': 'a', 'auth': 'a'}})
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_webpush.assert_not_called() # Use the mock object directly

def test_send_cancellation_reminders_no_subscription(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    mock_now = datetime(2025, 1, 1, 10, 0, 0)
    
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return mock_now

    mocker.patch('gabs_api_server.app.datetime', FakeDateTime)
    mock_webpush = mocker.patch('gabs_api_server.app.webpush') # Store the mock object

    class_date = mock_now.strftime("%Y-%m-%d")
    class_time = (mock_now + timedelta(hours=3, minutes=30)).strftime("%H:%M")
    
    # NO push subscription saved
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_webpush.assert_not_called() # Use the mock object directly
    
    # Check that reminder_sent flag is still set to 1 even if no sub found (to avoid retrying)
    # Assuming implementation marks it as sent even if no sub
    # Let's verify DB state
    reminders = database.get_live_bookings_for_reminder()
    assert len(reminders) == 0 # Should be marked as sent or handled

def test_send_cancellation_reminders_webpush_error(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    mock_now = datetime(2025, 1, 1, 10, 0, 0)
    
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return mock_now

    mocker.patch('gabs_api_server.app.datetime', FakeDateTime)
    mocker.patch('gabs_api_server.app.config.VAPID_PRIVATE_KEY', 'test_key')
    mocker.patch('gabs_api_server.app.config.VAPID_ADMIN_EMAIL', 'test@example.com')

    class_date = mock_now.strftime("%Y-%m-%d")
    class_time = (mock_now + timedelta(hours=3, minutes=30)).strftime("%H:%M")
    
    database.save_push_subscription(username, {'endpoint': 'a', 'keys': {'p256dh': 'a', 'auth': 'a'}})
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # Mock webpush to raise exception
    mocker.patch('gabs_api_server.app.webpush', side_effect=Exception("Webpush failed"))
    mock_logger_error = mocker.patch('gabs_api_server.app.logging.error')

    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_logger_error.assert_called_once()
    assert "Error sending cancellation reminder" in mock_logger_error.call_args[0][0]

def test_send_cancellation_reminders_webpush_gone_error(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    mock_now = datetime(2025, 1, 1, 10, 0, 0)
    
    class FakeDateTime(datetime):
        @classmethod
        def now(cls):
            return mock_now

    mocker.patch('gabs_api_server.app.datetime', FakeDateTime)
    mocker.patch('gabs_api_server.app.config.VAPID_PRIVATE_KEY', 'test_key')
    mocker.patch('gabs_api_server.app.config.VAPID_ADMIN_EMAIL', 'test@example.com')

    class_date = mock_now.strftime("%Y-%m-%d")
    class_time = (mock_now + timedelta(hours=3, minutes=30)).strftime("%H:%M")
    
    sub_endpoint = 'http://example.com/endpoint'
    database.save_push_subscription(username, {'endpoint': sub_endpoint, 'keys': {'p256dh': 'a', 'auth': 'a'}})
    database.add_live_booking(username, "Test Class", class_date, class_time)
    
    # Mock webpush to raise 410 GONE exception
    mocker.patch('gabs_api_server.app.webpush', side_effect=Exception("410 Gone"))
    mock_logger_error = mocker.patch('gabs_api_server.app.logging.error')
    mock_logger_info = mocker.patch('gabs_api_server.app.logging.info')

    # 2. Execute
    send_cancellation_reminders()
    
    # 3. Assert
    mock_logger_error.assert_called_once()
    # Verify subscription was deleted
    subs = database.get_push_subscriptions_for_user(username)
    assert len(subs) == 0

def test_reset_failed_bookings_flow(memory_db):
    # 1. Setup
    # Booking 1: Failed and old (should be reset)
    booking_id_failed = database.add_auto_booking("test_user", "Test Class", "10:00", "Monday", "Test Instructor")
    one_day_ago = int((datetime.now() - timedelta(hours=25)).timestamp())
    database.update_auto_booking_status(booking_id_failed, "failed", last_attempt_at=one_day_ago)

    # Booking 2: In Progress (should be reset immediately)
    booking_id_stuck = database.add_auto_booking("test_user", "Test Class 2", "12:00", "Tuesday", "Test Instructor")
    database.update_auto_booking_status(booking_id_stuck, "in_progress")

    # Booking 3: Failed but recent (should NOT be reset)
    booking_id_recent_fail = database.add_auto_booking("test_user", "Test Class 3", "14:00", "Wednesday", "Test Instructor")
    one_hour_ago = int((datetime.now() - timedelta(hours=1)).timestamp())
    database.update_auto_booking_status(booking_id_recent_fail, "failed", last_attempt_at=one_hour_ago)
    
    # 2. Execute
    reset_failed_bookings()

    # 3. Assert
    updated_booking_failed = database.get_auto_booking_by_id(booking_id_failed)
    assert updated_booking_failed[4] == 'pending'

    updated_booking_stuck = database.get_auto_booking_by_id(booking_id_stuck)
    assert updated_booking_stuck[4] == 'pending'

    updated_booking_recent = database.get_auto_booking_by_id(booking_id_recent_fail)
    assert updated_booking_recent[4] == 'failed'

def test_refresh_sessions_success(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    # Mock get_all_users
    mocker.patch('gabs_api_server.database.get_all_users', return_value=[username])
    
    # Mock get_scraper_instance
    mock_scraper = mocker.Mock()
    mock_scraper.get_my_bookings.return_value = [{"name": "Class", "date": "2025-01-01", "time": "10:00"}]
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    
    # Mock sync_live_bookings and touch_session
    mock_sync = mocker.patch('gabs_api_server.app.sync_live_bookings')
    mock_touch = mocker.patch('gabs_api_server.database.touch_session')

    # 2. Execute
    refresh_sessions()

    # 3. Assert
    mock_scraper.get_my_bookings.assert_called_once()
    mock_sync.assert_called_once_with(username, [{"name": "Class", "date": "2025-01-01", "time": "10:00"}])
    mock_touch.assert_called_once_with(username)

def test_refresh_sessions_no_users(memory_db, mocker):
    mocker.patch('gabs_api_server.database.get_all_users', return_value=[])
    mock_logging_info = mocker.patch('gabs_api_server.app.logging.info')
    
    refresh_sessions()
    
    assert "No users found" in mock_logging_info.call_args[0][0]

def test_refresh_sessions_scraper_fail(memory_db, mocker):
    mocker.patch('gabs_api_server.database.get_all_users', return_value=["test_user"])
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=None)
    mock_logging_warning = mocker.patch('gabs_api_server.app.logging.warning')
    
    refresh_sessions()
    
    mock_logging_warning.assert_called_once()
    assert "Could not get scraper instance" in mock_logging_warning.call_args[0][0]

def test_refresh_sessions_session_expired(memory_db, mocker):
    mocker.patch('gabs_api_server.database.get_all_users', return_value=["test_user"])
    mock_scraper = mocker.Mock()
    mock_scraper.get_my_bookings.side_effect = SessionExpiredError("Expired")
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mock_logging_info = mocker.patch('gabs_api_server.app.logging.info')
    
    refresh_sessions()
    
    # Should log that session was expired
    found_log = False
    for call in mock_logging_info.call_args_list:
        if "Session for test_user was expired" in call[0][0]:
            found_log = True
            break
    assert found_log

def test_refresh_sessions_unexpected_error(memory_db, mocker):
    mocker.patch('gabs_api_server.database.get_all_users', return_value=["test_user"])
    mock_scraper = mocker.Mock()
    mock_scraper.get_my_bookings.side_effect = Exception("Unexpected")
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mock_logging_error = mocker.patch('gabs_api_server.app.logging.error')
    
    refresh_sessions()
    
    mock_logging_error.assert_called_once()
    assert "An unexpected error occurred" in mock_logging_error.call_args[0][0]