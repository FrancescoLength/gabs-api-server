import threading
from datetime import datetime, timedelta
from unittest.mock import Mock
from gabs_api_server.services.auto_booking_service import (
    process_auto_bookings_job, _process_single_booking, MAX_BOOKING_WORKERS)
from gabs_api_server.app import app, debug_writer_queue, handle_session_expiration
from gabs_api_server import database, config
from gabs_api_server.scraper import SessionExpiredError


def test_process_auto_bookings_job_successful_booking(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Test Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Mock the Scraper object that get_scraper_instance will return
    mock_scraper_obj = mocker.Mock()
    mock_scraper_obj.find_and_book_class.return_value = {
        "status": "success",
        "class_name": class_name,
        "message": "Booking successful",
        "action": "booking",
        "html_content": ""
    }

    # Create a mock for the get_scraper_instance_func itself
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        # Pass the lambda function here
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'  # Status should be reset to pending
    assert updated_booking[10] is not None  # last_booked_date should be set

    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 1
    assert live_bookings[0][2] == class_name


def test_process_auto_bookings_job_stuck_in_progress_reset(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Stuck Class"
    target_time = "10:00"
    day_of_week = "Monday"

    # Create a booking that is 'in_progress' and old
    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")
    # Older than 10 min threshold
    old_timestamp = int((datetime.now() - timedelta(minutes=11)).timestamp())
    database.update_auto_booking_status(
        booking_id, 'in_progress', last_attempt_at=old_timestamp)

    # Mock the Scraper object that get_scraper_instance will return
    mock_scraper_obj = mocker.Mock()
    mock_scraper_obj.find_and_book_class.return_value = {
        "status": "success", "message": "Booking successful"}

    # Create a mock for the get_scraper_instance_func itself
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'  # Status should be reset to pending


def test_process_auto_bookings_job_booking_match_not_found_retry(
        memory_db,
        mocker):
    # Setup
    username = "test_user"
    class_name = "Missing Class"
    # Use a time 5 minutes from now and the current day to ensure it's within
    # the 48h booking window
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Mock the Scraper object that get_scraper_instance will return
    mock_scraper_obj = mocker.Mock()
    mock_scraper_obj.find_and_book_class.return_value = {
        "status": "error",
        "message": "Could not find a suitable match for 'Missing Class' at 11:00.",
        "html_content": "some html"}

    # Create a mock for the get_scraper_instance_func itself
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    # Should retry, so status is pending
    assert updated_booking[4] == 'pending'
    assert updated_booking[7] == 1  # retry_count should be 1


def test_process_auto_bookings_job_invalid_day_of_week(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Invalid Day Class"
    target_time = "09:00"
    day_of_week = "Funday"  # Invalid day

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Mock dependencies (simplified as they won't be called in this error path)
    mock_scraper_obj = mocker.Mock()
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'failed'  # Should be marked as failed
    # Max retry count should be set
    assert updated_booking[7] == config.MAX_AUTO_BOOK_RETRIES


def test_process_auto_bookings_job_already_booked_for_date(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Already Booked"
    target_time = "10:00"
    day_of_week = datetime.now().strftime("%A")
    current_date = datetime.now().strftime("%Y-%m-%d")

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")
    database.update_auto_booking_status(
        booking_id, last_booked_date=current_date)  # Already booked for today

    # Mock dependencies (they won't be called in this path)
    mock_scraper_obj = mocker.Mock()
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'  # Status should remain pending
    # last_booked_date should be unchanged
    assert updated_booking[10] == current_date


def test_process_auto_bookings_job_invalid_time_format(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Bad Time Class"
    target_time = "25:00"  # Invalid time format
    day_of_week = datetime.now().strftime("%A")

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Mock dependencies (simplified as they won't be called in this error path)
    mock_scraper_obj = mocker.Mock()
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'failed'  # Should be marked as failed
    # Max retry count should be set
    assert updated_booking[7] == config.MAX_AUTO_BOOK_RETRIES


def test_process_auto_bookings_job_too_early_to_book(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Early Class"
    # Set a target time far in the future (e.g., more than 48 hours from now)
    future_time = datetime.now() + timedelta(days=5)
    target_time = future_time.strftime("%H:%M")
    day_of_week = future_time.strftime("%A")

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Mock dependencies (they won't be called in this path)
    mock_scraper_obj = mocker.Mock()
    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'  # Status should remain pending
    # last_booked_date and retry_count should not be changed as no booking
    # attempt was made
    assert updated_booking[7] == 0


def test_process_auto_bookings_job_scraper_not_found(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "No Scraper Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Mock get_scraper_instance_func to return None
    mock_get_scraper_instance_func = mocker.Mock(return_value=None)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,  # Pass the mock here
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    assert updated_booking[4] == 'pending'  # Status should be pending (retry)
    assert updated_booking[7] == 1  # retry_count should be 1 (first attempt)


def test_process_auto_bookings_job_scraper_not_found_max_retries(
        memory_db,
        mocker):
    # Setup
    username = "test_user"
    class_name = "No Scraper Class Max Retries"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Set retry_count to MAX_AUTO_BOOK_RETRIES - 1 so the next attempt hits the limit
    # Note: The logic in auto_booking_service increments retry_count BEFORE checking the limit for the scraper case.
    # Logic: new_retry_count = retry_count + 1. If new_retry_count < MAX (3), update pending. Else failed.
    # So if we want it to fail, we need new_retry_count to be 3. So start with
    # 2.
    database.update_auto_booking_status(
        booking_id, 'pending', retry_count=config.MAX_AUTO_BOOK_RETRIES - 1)

    # Mock get_scraper_instance_func to return None
    mock_get_scraper_instance_func = mocker.Mock(return_value=None)

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    # Should be marked as failed because max retries reached
    assert updated_booking[4] == 'failed'
    assert updated_booking[7] == config.MAX_AUTO_BOOK_RETRIES


def test_process_auto_bookings_job_session_expired(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Session Expired Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Mock the Scraper object that get_scraper_instance will return
    mock_scraper_obj = mocker.Mock()
    # Configure find_and_book_class to raise SessionExpiredError
    mock_scraper_obj.find_and_book_class.side_effect = SessionExpiredError(
        "Session is stale")

    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)
    mocker.patch('gabs_api_server.app.logging.error')
    # mocker.patch('gabs_api_server.app.logging.info')
    mock_handle_session_expiration_func = mocker.patch(
        'gabs_api_server.app.handle_session_expiration')

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=mock_handle_session_expiration_func  # Pass the mock here
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    # Status should be reset to pending for retry
    assert updated_booking[4] == 'pending'
    assert updated_booking[7] == 1  # retry_count should be 1
    mock_handle_session_expiration_func.assert_called_once_with(
        username)  # Verify handler was called


def test_process_auto_bookings_job_generic_exception(memory_db, mocker):
    # Setup
    username = "test_user"
    class_name = "Generic Error Class"
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Mock the Scraper object that get_scraper_instance will return
    mock_scraper_obj = mocker.Mock()
    # Configure find_and_book_class to raise a generic exception
    mock_scraper_obj.find_and_book_class.side_effect = Exception(
        "A mysterious error occurred")

    mock_get_scraper_instance_func = mocker.Mock(return_value=mock_scraper_obj)

    booking_id = database.add_auto_booking(
        username, class_name, target_time, day_of_week, "instructor")

    # Execute
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper_instance_func,
        handle_session_expiration_func=handle_session_expiration
    )

    # Assert
    updated_booking = database.get_auto_booking_by_id(booking_id)
    # Should be marked as pending for retry
    assert updated_booking[4] == 'pending'
    assert updated_booking[7] == 1  # retry_count should be 1 (first attempt)


# --- Parallelization Tests ---


def test_parallel_bookings_different_users(memory_db, mocker):
    """Bookings for different users should be processed via ThreadPoolExecutor,
    allowing their I/O waits to overlap."""
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Create bookings for 3 different users
    id1 = database.add_auto_booking("user_a", "ClassA", target_time, day_of_week, "inst")
    id2 = database.add_auto_booking("user_b", "ClassB", target_time, day_of_week, "inst")
    id3 = database.add_auto_booking("user_c", "ClassC", target_time, day_of_week, "inst")

    # Track which threads process each user's booking
    thread_ids = {}

    original_process = _process_single_booking

    def tracking_process(booking_summary, *args, **kwargs):
        username = booking_summary[1]
        thread_ids[username] = threading.current_thread().ident
        return original_process(booking_summary, *args, **kwargs)

    mocker.patch(
        'gabs_api_server.services.auto_booking_service._process_single_booking',
        side_effect=tracking_process)

    # All scrapers return success
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_book_class.return_value = {
        "status": "success", "class_name": "Class",
        "message": "Booking successful", "action": "booking", "html_content": ""}
    mock_get_scraper = mocker.Mock(return_value=mock_scraper)

    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper,
        handle_session_expiration_func=handle_session_expiration
    )

    # All 3 users should have been processed
    assert len(thread_ids) == 3
    assert set(thread_ids.keys()) == {"user_a", "user_b", "user_c"}

    # Verify all bookings completed successfully
    for bid in [id1, id2, id3]:
        booking = database.get_auto_booking_by_id(bid)
        assert booking[10] is not None  # last_booked_date set


def test_parallel_same_user_stays_sequential(memory_db, mocker):
    """Multiple bookings for the same user should run sequentially in one thread."""
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    # Two bookings for the same user
    id1 = database.add_auto_booking("same_user", "Class1", target_time, day_of_week, "inst")
    id2 = database.add_auto_booking("same_user", "Class2", target_time, day_of_week, "inst")

    # Track order and thread of processing
    call_log = []

    original_process = _process_single_booking

    def tracking_process(booking_summary, *args, **kwargs):
        call_log.append({
            'booking_id': booking_summary[0],
            'thread': threading.current_thread().ident
        })
        return original_process(booking_summary, *args, **kwargs)

    mocker.patch(
        'gabs_api_server.services.auto_booking_service._process_single_booking',
        side_effect=tracking_process)

    mock_scraper = mocker.Mock()
    mock_scraper.find_and_book_class.return_value = {
        "status": "success", "class_name": "Class",
        "message": "Booking successful", "action": "booking", "html_content": ""}
    mock_get_scraper = mocker.Mock(return_value=mock_scraper)

    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper,
        handle_session_expiration_func=handle_session_expiration
    )

    # Both bookings should have been processed on the same thread
    assert len(call_log) == 2
    assert call_log[0]['thread'] == call_log[1]['thread']


def test_parallel_error_isolation(memory_db, mocker):
    """An error in one user's booking should not affect another user's booking."""
    target_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
    day_of_week = datetime.now().strftime("%A")

    id_good = database.add_auto_booking("good_user", "GoodClass", target_time, day_of_week, "inst")
    id_bad = database.add_auto_booking("bad_user", "BadClass", target_time, day_of_week, "inst")

    # Good user's scraper succeeds, bad user's scraper raises exception
    good_scraper = mocker.Mock()
    good_scraper.find_and_book_class.return_value = {
        "status": "success", "class_name": "GoodClass",
        "message": "Booking successful", "action": "booking", "html_content": ""}

    bad_scraper = mocker.Mock()
    bad_scraper.find_and_book_class.side_effect = Exception("Network failure")

    def get_scraper(username, *args, **kwargs):
        if username == "good_user":
            return good_scraper
        return bad_scraper

    mock_get_scraper = mocker.Mock(side_effect=get_scraper)

    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=mock_get_scraper,
        handle_session_expiration_func=handle_session_expiration
    )

    # Good user's booking should succeed regardless of bad user's failure
    good_booking = database.get_auto_booking_by_id(id_good)
    assert good_booking[10] is not None  # last_booked_date set (success)

    # Bad user's booking should be marked for retry
    bad_booking = database.get_auto_booking_by_id(id_bad)
    assert bad_booking[4] == 'pending'  # Retry
    assert bad_booking[7] == 1  # retry_count incremented


def test_max_booking_workers_constant():
    """Verify MAX_BOOKING_WORKERS is set to a sensible value for Pi Zero W."""
    assert MAX_BOOKING_WORKERS == 3
