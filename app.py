import json
import os
import re
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
import queue
import threading

from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager, verify_jwt_in_request

from scraper import Scraper, SessionExpiredError
import config
import database
import crypto
from pywebpush import webpush

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from logging_config import setup_logging, LOG_FILE

# Configure logging
setup_logging()

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)
app.start_time = datetime.now()


# Explicitly define allowed origins for CORS
origins = [
    "https://gabs-bristol.vercel.app",  # Vercel frontend
    "http://localhost:3000",             # Local React dev server
    "http://localhost:5173",             # Local Vite dev server
    r"https://.*\.ngrok-free\.dev"        # Regex for ngrok tunnels
]
CORS(app, resources={r"/api/*": {"origins": origins}}, supports_credentials=True)

database.init_db()

# --- Debug File Writer Thread ---
debug_writer_queue = queue.Queue()

def debug_file_writer():
    """A worker thread that writes debug HTML files from a queue."""
    while True:
        try:
            # Wait indefinitely for an item
            filepath, content = debug_writer_queue.get()

            # A None item is the signal to stop (for graceful shutdown, not used with daemon)
            if filepath is None:
                break

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Successfully wrote debug file to {filepath}")
            debug_writer_queue.task_done()
        except Exception as e:
            logging.error(f"Error in debug file writer thread: {e}")

# Start the writer thread as a daemon so it exits when the main app exits
writer_thread = threading.Thread(target=debug_file_writer, daemon=True)
writer_thread.start()


# --- Session Management ---

def get_scraper_instance(username, password=None):
    """
    Gets a scraper instance for a user by loading their session from the database
    or creating a new one if in a login flow.
    This function does NOT use an in-memory cache.
    """
    encrypted_password, session_data = database.load_session(username)

    # Case 1: Login flow (password is provided)
    if password:
        try:
            scraper = Scraper(username, password)
            encrypted_pass = crypto.encrypt(password)
            # Save the new session to the database immediately
            database.save_session(username, encrypted_pass, scraper.to_dict())
            return scraper
        except Exception as e:
            logging.error(f"Failed to create new session for {username} during login: {e}")
            return None

    # Case 2: Existing session restoration (no password provided)
    if encrypted_password:
        try:
            password_to_use = crypto.decrypt(encrypted_password)
            scraper = Scraper(username, password_to_use, session_data=session_data)
            # The session is not saved here to avoid writing to DB on every request.
            # Session saving is handled by the login flow and the refresh_sessions job.
            return scraper
        except Exception as e:
            logging.error(f"Failed to restore session for {username}: {e}")
            return None

    # Case 3: No password and no stored session
    logging.warning(f"No session or credentials found for {username}. Cannot create scraper instance.")
    return None

def handle_session_expiration(username):
    """
    Handles a SessionExpiredError. It logs the event and relies on the proactive
    `refresh_sessions` job or the user logging in again to fix it.
    It does NOT attempt an immediate re-login to avoid blocking critical tasks.
    """
    logging.warning(f"Session for {username} has expired. A proactive refresh or user login is required.")
    # We don't raise an exception here, but return None to the caller in the scraper_endpoint wrapper
    # The wrapper will then return a 401 error to the client.
    return None


# --- APScheduler Configuration ---
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}')
}
scheduler = BackgroundScheduler(jobstores=jobstores)

def process_auto_bookings():
    with app.app_context():
        pending_bookings = database.get_pending_auto_bookings()
        now = datetime.now()

        for booking_summary in pending_bookings:
            booking_id = booking_summary[0]

            # Attempt to lock the booking before processing
            if not database.lock_auto_booking(booking_id):
                logging.debug(f"Booking {booking_id} is already being processed by another thread. Skipping.")
                continue

            try:
                # Refetch the full booking details now that we have the lock
                booking = database.get_auto_booking_by_id(booking_id)
                if not booking or booking[4] != 'in_progress': # status is at index 4
                    logging.warning(f"Could not refetch booking {booking_id} or status was not 'in_progress' after locking. Skipping.")
                    continue

                booking_id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date = booking

                today = datetime.now()
                days_of_week_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
                target_day_index = days_of_week_map[day_of_week]
                
                days_until_target = (target_day_index - today.weekday() + 7) % 7
                next_occurrence_date = today + timedelta(days=days_until_target)
                current_target_date = next_occurrence_date.strftime("%Y-%m-%d")

                if last_booked_date == current_target_date:
                    database.update_auto_booking_status(booking_id, 'pending') # Release the lock
                    continue

                target_datetime = datetime.strptime(f"{current_target_date} {target_time}", "%Y-%m-%d %H:%M")
                booking_time = int((target_datetime - timedelta(hours=48)).timestamp())

                if booking_time > int(datetime.now().timestamp()):
                    database.update_auto_booking_status(booking_id, 'pending') # Release the lock
                    continue
                
                try:
                    user_scraper = get_scraper_instance(username)
                    if not user_scraper:
                        logging.warning(f"Scraper session for {username} not found for auto-booking {booking_id}. Re-login required.")
                        database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()))
                        continue

                    logging.info(f"Attempting to book class on {current_target_date} at {target_time} with {instructor} for user {username} (Booking ID: {booking_id})")
                    result = user_scraper.find_and_book_class(target_date_str=current_target_date, class_name=class_name, target_time=target_time, instructor=instructor)
                    
                    result_message = result.get('message', '').lower()
                    if result.get('status') == 'success' or (result.get('status') == 'info' and ("already registered" in result_message or "waiting list" in result_message or "already booked" in result_message)):
                        
                        # Use the class name from the scraper result if available, otherwise use the original class name
                        booked_class_name = result.get('class_name', class_name)

                        database.update_auto_booking_status(booking_id, 'pending', last_booked_date=current_target_date, last_attempt_at=int(datetime.now().timestamp()), retry_count=0)
                        database.add_live_booking(username, booked_class_name, current_target_date, target_time, instructor, booking_id)
                        logging.info(f"Successfully processed booking for auto-booking {booking_id}. Status: {result.get('message')}")
                    else:
                        if 'Could not find a suitable match' in result.get('message', ''):
                            new_retry_count = (retry_count or 0) + 1
                            
                            html_content = result.get('html_content')
                            if html_content:
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                debug_filename = f"debug_booking_{booking_id}_{timestamp}.html"
                                debug_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), debug_filename)
                                # Put the file writing task into the queue instead of writing directly
                                debug_writer_queue.put((debug_filepath, html_content))
                                logging.info(f"Queued debug HTML for booking {booking_id} to be written to {debug_filename}")

                            if new_retry_count < 2:
                                database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                                logging.warning(f"Booking attempt failed for auto-booking {booking_id} (match not found). Retrying once. Result: {result.get('message')}")
                            else:
                                database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                                logging.error(f"Booking attempt failed for auto-booking {booking_id} after 2 attempts (match not found). Marking as failed. Result: {result.get('message')}")
                        else:
                            new_retry_count = (retry_count or 0) + 1
                            if new_retry_count < config.MAX_AUTO_BOOK_RETRIES:
                                database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                                logging.warning(f"Booking attempt failed for auto-booking {booking_id}. Retrying (attempt {new_retry_count}). Result: {result}")
                            else:
                                database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                                logging.error(f"Booking attempt failed for auto-booking {booking_id} after {new_retry_count} retries. Marking as failed. Result: {result}")
                except SessionExpiredError:
                    handle_session_expiration(username)
                    logging.warning(f"Session expired for {username} during auto-booking. Re-logged in, will retry on next cycle.")
                    database.update_auto_booking_status(booking_id, 'pending') # Release lock
                except Exception as e:
                    new_retry_count = (retry_count or 0) + 1
                    if new_retry_count < config.MAX_AUTO_BOOK_RETRIES:
                        database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.error(f"Error during booking attempt for auto-booking {booking_id}: {e}. Retrying (attempt {new_retry_count}).")
                    else:
                        database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.error(f"Error during booking attempt for auto-booking {booking_id}: {e}. Marking as failed after {new_retry_count} retries.")
            finally:
                # Safeguard to ensure the lock is always released.
                current_booking_state = database.get_auto_booking_by_id(booking_id)
                if current_booking_state and current_booking_state[4] == 'in_progress':
                     database.update_auto_booking_status(booking_id, 'pending')
                     logging.warning(f"Booking {booking_id} was left in 'in_progress' state and has been reset to 'pending'.")

def send_cancellation_reminders():
    with app.app_context():
        logging.info("Running send_cancellation_reminders job.")
        live_bookings_to_remind = database.get_live_bookings_for_reminder()
        now = datetime.now()

        for booking in live_bookings_to_remind:
            booking_id, username, class_name, class_date_str, class_time_str, instructor = booking

            class_datetime = datetime.strptime(f"{class_date_str} {class_time_str}", "%Y-%m-%d %H:%M")

            time_until_class = class_datetime - now
            
            # Define the reminder window: exactly 3 hours and 30 minutes before the class
            reminder_threshold = timedelta(hours=3, minutes=30)
            
            # Check if current time is within a small window around the reminder_threshold
            # To avoid missing the exact second, we check a small interval, e.g., +/- 1 minute
            if timedelta(hours=3, minutes=25) <= time_until_class <= timedelta(hours=3, minutes=35):
                logging.info(f"Sending cancellation reminder for live booking ID {booking_id} for user {username}.")
                subscriptions = database.get_push_subscriptions_for_user(username)
                
                if subscriptions:
                    for sub in subscriptions:
                        try:
                            webpush(
                                subscription_info=sub,
                                data=json.dumps({
                                    "title": "GABS Reminder",
                                    "body": f"If today you can't make {class_name} class at {class_time_str}, don't forget to cancel it within ~30 minutes!",
                                    "icon": "/favicon.png",
                                    "badge": "/favicon.png",
                                    "tag": f"cancellation-reminder-{booking_id}",
                                    "url": "/live-booking"
                                }),
                                vapid_private_key=config.VAPID_PRIVATE_KEY,
                                vapid_claims={"sub": f"mailto:{config.VAPID_ADMIN_EMAIL}"}
                            )
                            logging.info(f"Cancellation reminder sent to {username} for live booking ID {booking_id}.")
                            database.update_live_booking_reminder_status(booking_id, reminder_sent=1)
                        except Exception as e:
                            logging.error(f"Error sending cancellation reminder to {username} for live booking ID {booking_id}: {e}")
                            if "410" in str(e): # GONE status, subscription is no longer valid
                                database.delete_push_subscription(sub['endpoint'])
                                logging.info(f"Deleted invalid push subscription for user {username}: {sub['endpoint']}")
                else:
                    logging.info(f"No push subscriptions found for {username} for live booking ID {booking_id}. Marking reminder as sent.")
                    database.update_live_booking_reminder_status(booking_id, reminder_sent=1)

            else:
                logging.debug(f"Live booking ID {booking_id} for {username} not within cancellation reminder window. Time until class: {time_until_class}")

def reset_failed_bookings():
    with app.app_context():
        logging.info("Running reset_failed_bookings job.")
        failed_bookings = database.get_failed_auto_bookings()
        now_timestamp = int(datetime.now().timestamp())
        reset_threshold_seconds = 24 * 60 * 60  # 24 hours

        for booking_id, last_attempt_at in failed_bookings:
            if last_attempt_at and (now_timestamp - last_attempt_at) > reset_threshold_seconds:
                logging.info(f"Resetting failed auto-booking ID {booking_id} to pending.")
                database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=None, retry_count=0)
            else:
                logging.debug(f"Failed auto-booking ID {booking_id} not yet eligible for reset.")

def refresh_sessions():
    """
    Proactively refreshes all user sessions and syncs their live bookings.
    """
    with app.app_context():
        users = database.get_all_users()
        if not users:
            logging.info("No users found in the database to refresh sessions for.")
            return

        for username in users:
            try:
                scraper = get_scraper_instance(username)
                if scraper:
                    # Perform a lightweight, safe operation to check session validity
                    bookings = scraper.get_my_bookings()
                    database.touch_session(username) # Session is valid, so we touch the timestamp
                    sync_live_bookings(username, bookings)
                    logging.debug(f"Session for {username} is valid and bookings synced.")
                else:
                    logging.warning(f"Could not get scraper instance for {username} during session refresh.")
            except SessionExpiredError:
                # The decorator on the scraper method already handled the re-login
                logging.info(f"Session for {username} was expired and has been refreshed by the scraper.")
            except Exception as e:
                logging.error(f"An unexpected error occurred while refreshing session for {username}: {e}")

app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
app.config["JWT_TOKEN_LOCATION"] = ["headers"]
jwt = JWTManager(app)

# --- API Endpoints ---

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        current_user = get_jwt_identity()
        if current_user != config.ADMIN_EMAIL:
            return jsonify({"error": "Admins only!"}), 403
        return fn(*args, **kwargs)
    return wrapper

@app.route('/api/login', methods=['POST'])
@limiter.limit("10/minute")
def login_user():
    data = request.get_json()
    username = data.get('username', None)
    password = data.get('password', None)

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    try:
        logging.info(f"Login attempt for user: {username}")
        user_scraper = get_scraper_instance(username, password)
        if not user_scraper:
            raise Exception("Failed to create scraper instance.")
        
        access_token = create_access_token(identity=username)
        logging.info(f"Successfully created session and token for {username}")
        return jsonify(access_token=access_token)
    except Exception as e:
        logging.error(f"Failed login for user {username}: {e}")
        return jsonify({"error": "Invalid credentials or login failed"}), 401

@app.route("/api/logout", methods=["POST"])
@jwt_required()
def logout_user():
    current_user = get_jwt_identity()
    database.delete_session(current_user)
    logging.info(f"Removed session for user: {current_user}")
    return jsonify({"message": "Successfully logged out"})

# --- Wrapper for scraper endpoints ---
def scraper_endpoint(f):
    @wraps(f)
    @jwt_required()
    def decorated_function(*args, **kwargs):
        current_user = get_jwt_identity()
        try:
            user_scraper = get_scraper_instance(current_user)
            if not user_scraper:
                return jsonify({"error": "Session not found. Please log in again."}), 401
            return f(user_scraper, *args, **kwargs)
        except SessionExpiredError:
            # This is now the primary failure point if a session is truly expired and couldn't be revived.
            # The handle_session_expiration function is called, which just logs the issue.
            # We then return a 401 to force the user to log in again via the client.
            handle_session_expiration(current_user)
            return jsonify({"error": "Your session has expired. Please log in again."}), 401
        except Exception as e:
            logging.error(f"Unhandled error in scraper endpoint for user {current_user}: {e}")
            return jsonify({"error": "An internal server error occurred."}), 500
    return decorated_function

@app.route('/api/classes', methods=['GET'])
@scraper_endpoint
def get_available_classes(user_scraper):
    classes = user_scraper.get_classes(days_in_advance=3)
    return jsonify(classes)

@app.route('/api/book', methods=['POST'])
@scraper_endpoint
def book_class(user_scraper):
    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date')
    target_time = data.get('time')
    if not all([class_name, target_date, target_time]):
        return jsonify({"error": "class_name, date, and time are required."} ), 400
    
    logging.info(f"User {user_scraper.username} attempting to book class {class_name} on {target_date} at {target_time}")
    result = user_scraper.find_and_book_class(
        target_date_str=target_date, 
        class_name=class_name, 
        target_time=target_time
    )
    return jsonify(result), 200

@app.route('/api/cancel', methods=['POST'])
@scraper_endpoint
def cancel_booking(user_scraper):
    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date')
    target_time = data.get('time')
    if not class_name or not target_date or not target_time:
        return jsonify({"error": "class_name, date, and time are required."} ), 400
    
    logging.info(f"User {user_scraper.username} attempting to cancel class {class_name} on {target_date} at {target_time}")
    result = user_scraper.find_and_cancel_booking(class_name, target_date, target_time)
    
    if result.get('status') == 'success':
        database.delete_live_booking(user_scraper.username, class_name, target_date, target_time)
        logging.info(f"Deleted live booking for {user_scraper.username}: {class_name} on {target_date} at {target_time} from database.")

    return jsonify(result), 200

@app.route('/api/bookings', methods=['GET'])
@scraper_endpoint
def get_my_bookings(user_scraper):
    bookings = user_scraper.get_my_bookings()
    sync_live_bookings(user_scraper.username, bookings)
    database.touch_session(user_scraper.username) # Session is valid, so we touch the timestamp
    return jsonify(bookings)

def sync_live_bookings(username, scraped_bookings):
    """
    Synchronizes the live_bookings table for a user with a fresh list of scraped bookings.
    """
    # 1. Get all current live bookings for the user from the database
    db_bookings_raw = database.get_live_bookings_for_user(username)
    db_bookings = set()
    db_bookings_map = {}  # Map to store original case and id
    for b in db_bookings_raw:
        # Create a unique tuple for each booking in lowercase
        key = (b[2].lower(), b[3], b[4]) # class_name, class_date, class_time
        db_bookings.add(key)
        db_bookings_map[key] = {'name': b[2], 'id': b[0]} # Store original class name and id

    # 2. Get all scraped bookings
    scraped_bookings_set = set()
    scraped_bookings_map = {} # Map to store original case
    for booking in scraped_bookings:
        class_name = booking.get('name')
        class_date_raw = booking.get('date')
        class_time = booking.get('time')
        
        if class_name and class_date_raw and class_time:
            try:
                date_part = ' '.join(class_date_raw.split(' ')[1:])
                date_part = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_part)
                current_year = datetime.now().year
                parsed_date = datetime.strptime(f"{date_part} {current_year}", "%d %B %Y")
                class_date = parsed_date.strftime("%Y-%m-%d")
                key = (class_name.lower(), class_date, class_time)
                scraped_bookings_set.add(key)
                scraped_bookings_map[key] = class_name # Store original class name
            except Exception as e:
                logging.error(f"Error parsing date '{class_date_raw}' during sync: {e}")
                continue

    # 3. Find bookings to add, to delete, and to check for case changes
    bookings_to_add = scraped_bookings_set - db_bookings
    bookings_to_delete = db_bookings - scraped_bookings_set
    bookings_to_check = db_bookings.intersection(scraped_bookings_set)

    # 4. Check for case changes in existing bookings
    for key in bookings_to_check:
        scraped_name = scraped_bookings_map[key]
        db_info = db_bookings_map[key]
        db_name = db_info['name']
        
        if scraped_name != db_name:
            booking_id = db_info['id']
            database.update_live_booking_name(booking_id, scraped_name)
            logging.info(f"Updated class name case for booking ID {booking_id} from '{db_name}' to '{scraped_name}'.")

    # 5. Add new bookings
    for key in bookings_to_add:
        class_name_lower, class_date, class_time = key
        class_name_original = scraped_bookings_map[key]
        
        # Find the full booking details from the original scraped list
        full_booking = next((b for b in scraped_bookings if b.get('name', '').lower() == class_name_lower and b.get('time') == class_time), None)
        instructor = full_booking.get('instructor') if full_booking else None
        
        if not database.live_booking_exists(username, class_name_original, class_date, class_time):
            database.add_live_booking(username, class_name_original, class_date, class_time, instructor)
            logging.info(f"Added live booking for {username}: {class_name_original} on {class_date} at {class_time} to database.")

    # 6. Delete old bookings
    for key in bookings_to_delete:
        class_name_lower, class_date, class_time = key
        class_name_original = db_bookings_map[key]['name']
        database.delete_live_booking(username, class_name_original, class_date, class_time)
        logging.info(f"Deleted stale live booking for {username}: {class_name_original} on {class_date} at {class_time} from database.")


@app.route('/api/static_classes', methods=['GET'])
def get_static_classes():
    # This endpoint does not require authentication or a scraper instance
    STATIC_TIMETABLE_PATH = os.path.join(os.path.dirname(__file__), 'static_timetable.json')
    if os.path.exists(STATIC_TIMETABLE_PATH):
        with open(STATIC_TIMETABLE_PATH, 'r') as f:
            static_classes_data = json.load(f)
        return jsonify(static_classes_data)
    else:
        logging.warning(f"Static timetable file not found at {STATIC_TIMETABLE_PATH}")
        return jsonify({"error": "Static timetable not found."} ), 404

@app.route('/api/schedule_auto_book', methods=['POST'])
@jwt_required()
def schedule_auto_book():
    current_user = get_jwt_identity()
    data = request.get_json()
    class_name = data.get('class_name')
    target_time_str = data.get('time')
    day_of_week = data.get('day_of_week')
    instructor = data.get('instructor')

    if not all([class_name, target_time_str, day_of_week, instructor]):
        return jsonify({"error": "class_name, time, day_of_week, and instructor are required."} ), 400

    try:
        booking_id = database.add_auto_booking(
            current_user, class_name, target_time_str, day_of_week, instructor
        )
        logging.info(f"Recurring auto-booking scheduled for user {current_user}: Class {class_name} on {day_of_week} at {target_time_str}. Booking ID: {booking_id}")
        return jsonify({"message": "Recurring auto-booking scheduled successfully!", "booking_id": booking_id}), 201
    except Exception as e:
        logging.error(f"Error scheduling auto-booking for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred. Contact Administrator."} ), 500

@app.route('/api/auto_bookings', methods=['GET'])
@jwt_required()
def get_auto_bookings():
    current_user = get_jwt_identity()
    try:
        bookings = database.get_auto_bookings_for_user(current_user)
        booking_list = []
        for b in bookings:
            booking_list.append({
                "id": b[0],
                "username": b[1],
                "class_name": b[2],
                "target_time": b[3],
                "status": b[4],
                "created_at": b[5],
                "last_attempt_at": b[6],
                "retry_count": b[7],
                "day_of_week": b[8],
                "instructor": b[9],
                "last_booked_date": b[10]
            })
        return jsonify(booking_list), 200
    except Exception as e:
        logging.error(f"Error retrieving auto-bookings for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."} ), 500

@app.route('/api/cancel_auto_book', methods=['POST'])
@jwt_required()
def cancel_auto_book():
    current_user = get_jwt_identity()
    data = request.get_json()
    booking_id = data.get('booking_id')

    if not booking_id:
        return jsonify({"error": "booking_id is required."} ), 400

    try:
        if database.cancel_auto_booking(booking_id, current_user):
            logging.info(f"Auto-booking ID {booking_id} cancelled by user {current_user}.")
            return jsonify({"message": f"Recurring auto-booking cancelled successfully!"} ), 200
        else:
            return jsonify({"error": "Booking not found or not authorized to cancel. Contact Administrator"} ), 404
    except Exception as e:
        logging.error(f"Error cancelling auto-booking ID {booking_id} for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."} ), 500

@app.route('/api/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    return config.VAPID_PUBLIC_KEY, 200

@app.route('/api/subscribe-push', methods=['POST'])
@jwt_required()
def subscribe_push():
    current_user = get_jwt_identity()
    subscription_info = request.get_json()

    if not subscription_info:
        return jsonify({"error": "Subscription info is required."} ), 400

    try:
        new_subscription = database.save_push_subscription(current_user, subscription_info)
        if new_subscription:
            logging.info(f"Push subscription saved for user: {current_user}")
        return jsonify({"message": "Push subscription successful"}), 201
    except Exception as e:
        logging.error(f"Error saving push subscription for user {current_user}: {e}")
        return jsonify({"error": "Failed to save push subscription."} ), 500

# --- Admin Endpoints ---

@app.route('/api/admin/logs', methods=['GET'])
@admin_required
def get_logs():
    log_pattern = re.compile(r'^(\S+ \S+) - (\w+) - (.*)')
    try:
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
            parsed_logs = []
            for line in reversed(lines[-100:]):
                match = log_pattern.match(line.strip())
                if match:
                    parsed_logs.append({
                        "timestamp": match.group(1),
                        "level": match.group(2),
                        "message": match.group(3)
                    })
                elif line.strip():
                    if parsed_logs and parsed_logs[-1]['level'] in ['RAW', 'RAW_MULTI']:
                        parsed_logs[-1]['message'] += '\n' + line.strip()
                    else:
                         parsed_logs.append({
                            "timestamp": "",
                            "level": "RAW",
                            "message": line.strip()
                        })
            return jsonify({"logs": parsed_logs})
    except FileNotFoundError:
        return jsonify({"error": "Log file not found."} ), 404

@app.route('/api/admin/auto_bookings', methods=['GET'])
@admin_required
def get_all_auto_bookings():
    bookings_raw = database.get_all_auto_bookings()
    bookings_formatted = []
    for b in bookings_raw:
        bookings_formatted.append({
            "id": b["id"],
            "username": b["username"],
            "class_name": b["class_name"],
            "target_time": b["target_time"],
            "status": b["status"],
            "created_at": b["created_at"],
            "last_attempt_at": b["last_attempt_at"],
            "retry_count": b["retry_count"],
            "day_of_week": b["day_of_week"],
            "instructor": b["instructor"],
            "last_booked_date": b["last_booked_date"]
        })
    return jsonify(bookings_formatted)

@app.route('/api/admin/live_bookings', methods=['GET'])
@admin_required
def get_all_live_bookings():
    bookings = database.get_all_live_bookings()
    return jsonify(bookings)

@app.route('/api/admin/push_subscriptions', methods=['GET'])
@admin_required
def get_all_push_subscriptions():
    subscriptions = database.get_all_push_subscriptions()
    return jsonify(subscriptions)

@app.route('/api/admin/sessions', methods=['GET'])
@admin_required
def get_all_sessions():
    sessions = database.get_all_sessions()
    return jsonify(sessions)

@app.route('/api/admin/status', methods=['GET'])
@admin_required
def get_status():
    uptime = datetime.now() - app.start_time
    ssh_command = None
    try:
        tunnels_response = requests.get('http://127.0.0.1:4040/api/tunnels')
        tunnels_response.raise_for_status()
        tunnels_data = tunnels_response.json()
        for tunnel in tunnels_data.get('tunnels', []):
            if tunnel.get('proto') == 'tcp':
                public_url = tunnel.get('public_url')
                if public_url:
                    # Extract host and port
                    match = re.match(r'tcp://(.+):(\d+)', public_url)
                    if match:
                        host = match.group(1)
                        port = match.group(2)
                        ssh_command = f"ssh -p {port} gabs-admin@{host}"
                break
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch ngrok tunnels: {e}")

    return jsonify({
        "status": "ok",
        "uptime": str(uptime),
        "ssh_tunnel_command": ssh_command
    })



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)