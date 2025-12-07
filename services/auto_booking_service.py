# gabs_api_server/services/auto_booking_service.py

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
import json
import os
import queue # Added this import

# Imports from outside the package
# import app as app_module # We will pass app_instance instead
# import database
# import config
# from scraper import SessionExpiredError

# Relative imports from within gabs_api_server package
from .. import database
from .. import config
from ..scraper import Scraper, SessionExpiredError

logger = logging.getLogger(__name__)

# Define a type alias for the functions we expect to receive
ScraperInstanceGetter = Callable[[str, Optional[str]], Optional[Scraper]]
SessionExpirationHandler = Callable[[str], None]
DebugWriterQueue = queue.Queue # type: ignore # queue is imported by app but not here

def process_auto_bookings_job(
    app_instance: Any, # Flask app instance for app_context
    debug_writer_queue_instance: DebugWriterQueue,
    get_scraper_instance_func: ScraperInstanceGetter,
    handle_session_expiration_func: SessionExpirationHandler
) -> None:
    """
    Processes all pending auto-bookings, attempting to book classes
    based on their defined schedule. This function runs periodically
    as a scheduled job.
    """
    with app_instance.app_context():
        # First, check for any 'in_progress' bookings that might be stuck
        stuck_in_progress_bookings = database.get_stuck_bookings() # Re-using get_stuck_bookings
        now_timestamp = int(datetime.now().timestamp())
        in_progress_staleness_threshold_seconds = 10 * 60 # 10 minutes

        for booking_id, last_attempt_at, status in stuck_in_progress_bookings:
            if status == 'in_progress':
                # If last_attempt_at is available and it's too old, it's definitely stuck
                if last_attempt_at and (now_timestamp - last_attempt_at) > in_progress_staleness_threshold_seconds:
                    logger.warning(
                        f"Auto-booking ID {booking_id} has been stuck in 'in_progress' for "
                        f"more than {in_progress_staleness_threshold_seconds // 60} minutes. Resetting to 'pending'."
                    )
                    database.update_auto_booking_status(
                        booking_id, 'pending', last_attempt_at=now_timestamp, retry_count=0
                    )
                elif not last_attempt_at:
                    # If last_attempt_at is None for an 'in_progress' booking, it's also stuck
                    logger.warning(
                        f"Auto-booking ID {booking_id} found in 'in_progress' state with no 'last_attempt_at'. "
                        f"Resetting to 'pending'."
                    )
                    database.update_auto_booking_status(
                        booking_id, 'pending', last_attempt_at=now_timestamp, retry_count=0
                    )

        # Now process legitimately pending bookings
        pending_bookings = database.get_pending_auto_bookings()
        
        for booking_summary in pending_bookings:
            booking_id: int = booking_summary[0] # type: ignore

            # Attempt to lock the booking before processing
            if not database.lock_auto_booking(booking_id):
                logger.warning(
                    f"Booking {booking_id} is already in 'in_progress' state or could not be locked. Skipping for now."
                )
                continue

            try:
                # Refetch the full booking details now that we have the lock
                booking_details = database.get_auto_booking_by_id(booking_id)
                if not booking_details or booking_details[4] != 'in_progress':  # status is at index 4
                    logger.warning(
                        f"Could not refetch booking {booking_id} or status was not 'in_progress' after locking. Skipping."
                    )
                    continue

                # Unpack the booking details (assuming the same tuple structure as before)
                (
                    booking_id, username, class_name, target_time, status, created_at, # type: ignore
                    last_attempt_at, retry_count, day_of_week, instructor, last_booked_date # type: ignore
                ) = booking_details # type: ignore
                # print(f"DEBUG_INTERNAL: Inside job. retry_count from DB: {retry_count}") # DEBUG PRINT

                today = datetime.now()
                days_of_week_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
                
                # Calculate the next occurrence date
                target_day_index = days_of_week_map.get(day_of_week)
                if target_day_index is None:
                    logger.error(f"Invalid day_of_week '{day_of_week}' for booking {booking_id}. Skipping.")
                    database.update_auto_booking_status(
                        booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=config.MAX_AUTO_BOOK_RETRIES # type: ignore
                    )
                    continue

                days_until_target = (target_day_index - today.weekday() + 7) % 7
                next_occurrence_date = today + timedelta(days=days_until_target)
                current_target_date = next_occurrence_date.strftime("%Y-%m-%d")

                # If this class was already booked for this date, just reset status to pending and continue
                if last_booked_date == current_target_date:
                    database.update_auto_booking_status(booking_id, 'pending')  # Release the lock
                    continue

                # Calculate the booking window (e.g., 48 hours before class starts)
                try:
                    target_class_datetime = datetime.strptime(f"{current_target_date} {target_time}", "%Y-%m-%d %H:%M")
                except ValueError:
                    logger.error(f"Invalid target_time '{target_time}' or current_target_date '{current_target_date}' for booking {booking_id}. Skipping.")
                    database.update_auto_booking_status(
                        booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=config.MAX_AUTO_BOOK_RETRIES # type: ignore
                    )
                    continue

                booking_window_start = target_class_datetime - timedelta(hours=48)

                if booking_window_start > today:
                    database.update_auto_booking_status(booking_id, 'pending')  # Release the lock
                    continue # Too early to book

                # Attempt to book the class
                user_scraper = get_scraper_instance_func(username)
                if not user_scraper:
                    logger.warning(f"Scraper session for {username} not found for auto-booking {booking_id}. Re-login required.")
                    new_retry_count = (retry_count or 0) + 1 # Increment retry_count
                    database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count) # Pass incremented retry_count
                    continue

                logger.info(
                    f"Attempting to book class on {current_target_date} at {target_time} with {instructor} for user {username} (Booking ID: {booking_id})"
                )
                
                result = user_scraper.find_and_book_class(
                    target_date_str=current_target_date, class_name=class_name,
                    target_time=target_time, instructor=instructor
                )

                result_message = result.get('message', '').lower()
                if result.get('status') == 'success' or (result.get('status') == 'info' and ("already registered" in result_message or "waiting list" in result_message or "already booked" in result_message)):
                    booked_class_name = result.get('class_name', class_name)
                    database.update_auto_booking_status(
                        booking_id, 'pending', last_booked_date=current_target_date,
                        last_attempt_at=int(today.timestamp()), retry_count=0
                    )
                    database.add_live_booking(username, booked_class_name, current_target_date, target_time, instructor, booking_id)
                    logger.info(f"Successfully processed booking for auto-booking {booking_id}. Status: {result.get('message')}")
                else:
                    new_retry_count = (retry_count or 0) + 1
                    html_content = result.get('html_content')
                    
                    if 'Could not find a suitable match' in result.get('message', ''):
                        if html_content:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            debug_filename = f"debug_booking_{booking_id}_{timestamp}.html"
                            debug_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), debug_filename)
                            # Put the file writing task into the queue instead of writing directly
                            debug_writer_queue_instance.put((debug_filepath, html_content))
                            logger.info(f"Queued debug HTML for booking {booking_id} to be written to {debug_filename}")

                        if new_retry_count < 2: # type: ignore
                            database.update_auto_booking_status(
                                booking_id, 'pending', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                            )
                            logger.warning(
                                f"Booking attempt failed for auto-booking {booking_id} (match not found). "
                                f"Retrying (attempt {new_retry_count}). Result: {result.get('message')}"
                            )
                        else:
                            database.update_auto_booking_status(
                                booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                            )
                            logger.error(
                                f"Booking attempt failed for auto-booking {booking_id} after 2 attempts (match not found). "
                                f"Marking as failed. Result: {result.get('message')}"
                            )
                    else:
                        if new_retry_count < config.MAX_AUTO_BOOK_RETRIES: # type: ignore
                            database.update_auto_booking_status(
                                booking_id, 'pending', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                            )
                            logger.warning(
                                f"Booking attempt failed for auto-booking {booking_id}. "
                                f"Retrying (attempt {new_retry_count}). Result: {result}"
                            )
                        else:
                            database.update_auto_booking_status(
                                booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                            )
                            logger.error(
                                f"Booking attempt failed for auto-booking {booking_id} after {new_retry_count} retries. "
                                f"Marking as failed. Result: {result}"
                            )
            except SessionExpiredError:
                handle_session_expiration_func(username)
                logger.warning(f"Session expired for {username} during auto-booking. Re-logged in, will retry on next cycle.")
                new_retry_count = (retry_count or 0) + 1
                database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count) # Release lock
            except Exception as e:
                new_retry_count = (retry_count or 0) + 1
                if new_retry_count < config.MAX_AUTO_BOOK_RETRIES: # type: ignore
                    database.update_auto_booking_status(
                        booking_id, 'pending', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                    )
                    logger.error(
                        f"Error during booking attempt for auto-booking {booking_id}: {e}. "
                        f"Retrying (attempt {new_retry_count})."
                    )
                else:
                    database.update_auto_booking_status(
                        booking_id, 'failed', last_attempt_at=int(today.timestamp()), retry_count=new_retry_count
                    )
                    logger.error(
                        f"Error during booking attempt for auto-booking {booking_id}: {e}. "
                        f"Marking as failed after {new_retry_count} retries."
                    )
            finally:
                # Safeguard to ensure the lock is always released.
                # Re-fetch the booking state in case it was modified by a retry logic path
                current_booking_state = database.get_auto_booking_by_id(booking_id)
                # logger.debug(f"DEBUG: Finally block for {booking_id}. Current status from DB: {current_booking_state[4] if current_booking_state else 'None'}.")
                if current_booking_state and current_booking_state[4] == 'in_progress':
                     database.update_auto_booking_status(booking_id, 'pending')
                     logger.warning(
                         f"Booking {booking_id} was left in 'in_progress' state and has been reset to 'pending' by finally block."
                     )