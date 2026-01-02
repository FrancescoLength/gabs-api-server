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
import queue
import threading
from typing import List, Dict, Any, Optional, Tuple, Callable

from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager, verify_jwt_in_request
from flasgger import Swagger

try:
    from .scraper import Scraper, SessionExpiredError
    from . import config
    from . import database
    from . import crypto
    from .services import auto_booking_service
    from .logging_config import setup_logging, LOG_FILE
except ImportError:
    from scraper import Scraper, SessionExpiredError
    import config
    import database
    import crypto
    from services import auto_booking_service
    from logging_config import setup_logging, LOG_FILE

from pywebpush import webpush

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Configure logging
setup_logging()

app = Flask(__name__)
swagger = Swagger(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)
app.start_time = datetime.now()


# Explicitly define allowed origins for CORS
origins: List[str] = [
    "https://gabs-bristol.vercel.app",  # Vercel frontend
    "http://localhost:3000",             # Local React dev server
    "http://localhost:5173",             # Local Vite dev server
    r"https://.*\.ngrok-free\.dev"        # Regex for ngrok tunnels
]
CORS(app, resources={r"/api/*": {"origins": origins}},
     supports_credentials=True)

database.init_db()

# --- Debug File Writer Thread ---
debug_writer_queue: queue.Queue[Tuple[str, str]] = queue.Queue()


def debug_file_writer() -> None:
    """A worker thread that writes debug HTML files from a queue."""
    while True:
        try:
            # Wait indefinitely for an item
            filepath, content = debug_writer_queue.get()

            # A None item is the signal to stop (for graceful shutdown, not used with daemon)
            if filepath is None:  # type: ignore
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

def get_scraper_instance(username: str, password: Optional[str] = None) -> Optional[Scraper]:
    """
    Gets a scraper instance for a user by loading their session from the database
    or creating a new one if in a login flow.
    This function does NOT use an in-memory cache.
    """
    encrypted_password: Optional[str]
    session_data: Optional[Dict[str, Any]]
    encrypted_password, session_data = database.load_session(username)

    # Case 1: Login flow (password is provided)
    if password:
        try:
            scraper = Scraper(username, password)
            encrypted_pass: str = crypto.encrypt(password)
            # Save the new session to the database immediately
            database.save_session(username, encrypted_pass, scraper.to_dict())
            return scraper
        except Exception as e:
            logging.error(
                f"Failed to create new session for {username} during login: {e}")
            return None

    # Case 2: Existing session restoration (no password provided)
    if encrypted_password:
        try:
            password_to_use: str = crypto.decrypt(encrypted_password)
            scraper = Scraper(username, password_to_use,
                              session_data=session_data)
            # The session is not saved here to avoid writing to DB on every request.
            # Session saving is handled by the login flow and the refresh_sessions job.
            return scraper
        except Exception as e:
            logging.error(f"Failed to restore session for {username}: {e}")
            return None

    # Case 3: No password and no stored session
    logging.warning(
        f"No session or credentials found for {username}. Cannot create scraper instance.")
    return None


def handle_session_expiration(username: str) -> None:
    """
    Handles a SessionExpiredError. It logs the event and relies on the proactive
    `refresh_sessions` job or the user logging in again to fix it.
    It does NOT attempt an immediate re-login to avoid blocking critical tasks.
    """
    logging.warning(
        f"Session for {username} has expired. A proactive refresh or user login is required.")
    # We don't raise an exception here, but return None to the caller in the scraper_endpoint wrapper
    # The wrapper will then return a 401 error to the client.
    return None


# --- APScheduler Configuration ---
jobstores: Dict[str, SQLAlchemyJobStore] = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}')
}
scheduler = BackgroundScheduler(jobstores=jobstores)

# Wrapper function for the moved auto-booking processing logic


def process_auto_bookings() -> None:
    auto_booking_service.process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=get_scraper_instance,
        handle_session_expiration_func=handle_session_expiration
    )


def send_cancellation_reminders() -> None:
    with app.app_context():
        logging.info("Running send_cancellation_reminders job.")
        live_bookings_to_remind: List[Tuple] = database.get_live_bookings_for_reminder(
        )
        now: datetime = datetime.now()

        for booking in live_bookings_to_remind:
            booking_id: int = booking[0]  # type: ignore
            username: str = booking[1]  # type: ignore
            class_name: str = booking[2]  # type: ignore
            class_date_str: str = booking[3]  # type: ignore
            class_time_str: str = booking[4]  # type: ignore
            # instructor: Optional[str] = booking[5]  # type: ignore

            class_datetime: datetime = datetime.strptime(
                f"{class_date_str} {class_time_str}", "%Y-%m-%d %H:%M")

            time_until_class: timedelta = class_datetime - now

            # Define the reminder window: exactly 3 hours and 30 minutes before the class

            # Check if current time is within a small window around the reminder_threshold
            # To avoid missing the exact second, we check a small interval, e.g., +/- 1 minute
            if timedelta(hours=3, minutes=25) <= time_until_class <= timedelta(hours=3, minutes=35):
                logging.info(
                    f"Sending cancellation reminder for live booking ID {booking_id} for user {username}.")
                subscriptions: List[Dict[str, Any]] = database.get_push_subscriptions_for_user(
                    username)

                if subscriptions:
                    for sub in subscriptions:
                        try:
                            webpush(
                                subscription_info=sub,
                                data=json.dumps({
                                    "title": "GABS Reminder",
                                    "body": (
                                        f"If today you can't make {class_name} class at {class_time_str}, "
                                        "don't forget to cancel it within ~30 minutes!"
                                    ),
                                    "icon": "/favicon.png",
                                    "badge": "/favicon.png",
                                    "tag": f"cancellation-reminder-{booking_id}",
                                    "url": "/live-booking"
                                }),
                                vapid_private_key=config.VAPID_PRIVATE_KEY,  # type: ignore
                                # type: ignore
                                vapid_claims={
                                    "sub": f"mailto:{config.VAPID_ADMIN_EMAIL}"}
                            )
                            logging.info(
                                f"Cancellation reminder sent to {username} for live booking ID {booking_id}.")
                            database.update_live_booking_reminder_status(
                                booking_id, reminder_sent=1)
                        except Exception as e:
                            logging.error(
                                f"Error sending cancellation reminder to {username} for live booking ID {booking_id}: {e}")
                            # GONE status, subscription is no longer valid
                            if "410" in str(e):
                                database.delete_push_subscription(
                                    sub['endpoint'])
                                logging.info(
                                    f"Deleted invalid push subscription for user {username}: {sub['endpoint']}")
                else:
                    logging.info(
                        f"No push subscriptions found for {username} for live booking ID {booking_id}. "
                        f"Marking reminder as sent.")
                    database.update_live_booking_reminder_status(
                        booking_id, reminder_sent=1)

            else:
                logging.debug(
                    f"Live booking ID {booking_id} for {username} not within cancellation reminder window. "
                    f"Time until class: {time_until_class}")


def reset_failed_bookings() -> None:
    with app.app_context():
        logging.info("Running reset_failed_bookings job.")
        stuck_bookings: List[Tuple] = database.get_stuck_bookings()
        now_timestamp: int = int(datetime.now().timestamp())
        reset_threshold_seconds: int = 24 * 60 * 60  # 24 hours

        for booking_id, last_attempt_at, status in stuck_bookings:  # type: ignore
            if status == 'in_progress':
                logging.warning(
                    f"Auto-booking ID {booking_id} found stuck in 'in_progress' state. Resetting to 'pending'.")
                database.update_auto_booking_status(
                    booking_id, 'pending', last_attempt_at=None, retry_count=0)
            elif status == 'failed':
                # type: ignore
                if last_attempt_at and (now_timestamp - last_attempt_at) > reset_threshold_seconds:
                    logging.info(
                        f"Resetting failed auto-booking ID {booking_id} to pending.")
                    database.update_auto_booking_status(
                        booking_id, 'pending', last_attempt_at=None, retry_count=0)
                else:
                    logging.debug(
                        f"Failed auto-booking ID {booking_id} not yet eligible for reset.")


def refresh_sessions() -> None:
    """
    Proactively refreshes all user sessions and syncs their live bookings.
    """
    with app.app_context():
        users: List[str] = database.get_all_users()
        if not users:
            logging.info(
                "No users found in the database to refresh sessions for.")
            return

        for username in users:
            try:
                scraper: Optional[Scraper] = get_scraper_instance(username)
                if scraper:
                    # Perform a lightweight, safe operation to check session validity
                    bookings: List[Dict[str, Any]] = scraper.get_my_bookings()
                    # Session is valid, so we touch the timestamp
                    database.touch_session(username)
                    sync_live_bookings(username, bookings)
                    logging.debug(
                        f"Session for {username} is valid and bookings synced.")
                else:
                    logging.warning(
                        f"Could not get scraper instance for {username} during session refresh.")
            except SessionExpiredError:
                # The decorator on the scraper method already handled the re-login
                logging.info(
                    f"Session for {username} was expired and has been refreshed by the scraper.")
            except Exception as e:
                logging.error(
                    f"An unexpected error occurred while refreshing session for {username}: {e}")


app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY  # type: ignore
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
app.config["JWT_TOKEN_LOCATION"] = ["headers"]
jwt = JWTManager(app)

# --- API Endpoints ---


def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        verify_jwt_in_request()
        current_user: str = get_jwt_identity()  # type: ignore
        if current_user != config.ADMIN_EMAIL:  # type: ignore
            return jsonify({"error": "Admins only!"}), 403
        return fn(*args, **kwargs)
    return wrapper


@app.route('/api/login', methods=['POST'])
@limiter.limit("10/minute")
def login_user() -> Tuple[Any, int]:
    """
    Authenticate a user and return a JWT access token.
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - username
            - password
          properties:
            username:
              type: string
              description: User's email/username
            password:
              type: string
              description: User's password
    responses:
      200:
        description: Login successful
        schema:
          type: object
          properties:
            access_token:
              type: string
      400:
        description: Missing username or password
      401:
        description: Invalid credentials or login failed
    """
    data: Dict[str, Any] = request.get_json()  # type: ignore
    username: Optional[str] = data.get('username')
    password: Optional[str] = data.get('password')

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    try:
        logging.info(f"Login attempt for user: {username}")
        user_scraper: Optional[Scraper] = get_scraper_instance(
            username, password)
        if not user_scraper:
            raise Exception("Failed to create scraper instance.")

        access_token: str = create_access_token(identity=username)
        logging.info(f"Successfully created session and token for {username}")
        return jsonify(access_token=access_token), 200
    except Exception as e:
        logging.error(f"Failed login for user {username}: {e}")
        return jsonify({"error": "Invalid credentials or login failed"}), 401


@app.route("/api/logout", methods=["POST"])
@jwt_required()
def logout_user() -> Tuple[Any, int]:
    current_user: str = get_jwt_identity()  # type: ignore
    database.delete_session(current_user)
    logging.info(f"Removed session for user: {current_user}")
    return jsonify({"message": "Successfully logged out"}), 200

# --- Wrapper for scraper endpoints ---


def scraper_endpoint(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        verify_jwt_in_request()  # Ensure JWT is present and valid
        current_user: str = get_jwt_identity()  # type: ignore
        try:
            user_scraper: Optional[Scraper] = get_scraper_instance(
                current_user)
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
            logging.error(
                f"Unhandled error in scraper endpoint for user {current_user}: {e}")
            return jsonify({"error": "An internal server error occurred."}), 500
    return decorated_function


@app.route('/api/classes', methods=['GET'])
@scraper_endpoint
def get_available_classes(user_scraper: Scraper) -> Tuple[Any, int]:
    """
    Get available classes for the next 3 days.
    ---
    tags:
      - Classes
    security:
      - Bearer: []
    responses:
      200:
        description: List of available classes
      401:
        description: Session expired or invalid
    """
    classes: List[Dict[str, Any]] = user_scraper.get_classes(days_in_advance=3)
    return jsonify(classes), 200


@app.route('/api/book', methods=['POST'])
@scraper_endpoint
def book_class(user_scraper: Scraper) -> Tuple[Any, int]:
    """
    Book a specific class.
    ---
    tags:
      - Booking
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - class_name
            - date
            - time
          properties:
            class_name:
              type: string
            date:
              type: string
              format: date
              example: "2025-01-01"
            time:
              type: string
              example: "10:00"
    responses:
      200:
        description: Booking result
      400:
        description: Missing parameters
      401:
        description: Session expired
    """
    data: Dict[str, Any] = request.get_json()  # type: ignore
    class_name: Optional[str] = data.get('class_name')
    target_date: Optional[str] = data.get('date')
    target_time: Optional[str] = data.get('time')
    if not all([class_name, target_date, target_time]):
        return jsonify({"error": "class_name, date, and time are required."}), 400

    logging.info(
        f"User {user_scraper.username} attempting to book class {class_name} on {target_date} at {target_time}")
    result: Dict[str, Any] = user_scraper.find_and_book_class(  # type: ignore
        target_date_str=target_date,  # type: ignore
        class_name=class_name,  # type: ignore
        target_time=target_time  # type: ignore
    )
    return jsonify(result), 200


@app.route('/api/cancel', methods=['POST'])
@scraper_endpoint
def cancel_booking(user_scraper: Scraper) -> Tuple[Any, int]:
    data: Dict[str, Any] = request.get_json()  # type: ignore
    class_name: Optional[str] = data.get('class_name')
    target_date: Optional[str] = data.get('date')
    target_time: Optional[str] = data.get('time')
    if not class_name or not target_date or not target_time:
        return jsonify({"error": "class_name, date, and time are required."}), 400

    logging.info(
        f"User {user_scraper.username} attempting to cancel class {class_name} on {target_date} at {target_time}")
    result: Dict[str, Any] = user_scraper.find_and_cancel_booking(
        class_name, target_date, target_time)  # type: ignore

    if result.get('status') == 'success':
        database.delete_live_booking(
            user_scraper.username, class_name, target_date, target_time)  # type: ignore
        # type: ignore
        logging.info(
            f"Deleted live booking for {user_scraper.username}: {class_name} on {target_date} at {target_time} from database.")

    return jsonify(result), 200


@app.route('/api/bookings', methods=['GET'])
@scraper_endpoint
def get_my_bookings(user_scraper: Scraper) -> Tuple[Any, int]:
    """
    Get the user's current bookings.
    ---
    tags:
      - Booking
    security:
      - Bearer: []
    responses:
      200:
        description: List of booked classes
      401:
        description: Session expired
    """
    bookings: List[Dict[str, Any]] = user_scraper.get_my_bookings()
    sync_live_bookings(user_scraper.username, bookings)
    # Session is valid, so we touch the timestamp
    database.touch_session(user_scraper.username)
    return jsonify(bookings), 200


def sync_live_bookings(username: str, scraped_bookings: List[Dict[str, Any]]) -> None:
    """
    Synchronizes the live_bookings table for a user with a fresh list of scraped bookings.
    """
    # 1. Get all current live bookings for the user from the database
    db_bookings_raw: List[Tuple] = database.get_live_bookings_for_user(
        username)
    db_bookings: set[Tuple[str, str, str]] = set()
    # Map to store original case and id
    db_bookings_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for b in db_bookings_raw:
        # Create a unique tuple for each booking in lowercase
        # class_name, class_date, class_time # type: ignore
        key = (b[2].lower(), b[3], b[4])
        db_bookings.add(key)
        # Store original class name and id # type: ignore
        db_bookings_map[key] = {'name': b[2], 'id': b[0]}

    # 2. Get all scraped bookings
    scraped_bookings_set: set[Tuple[str, str, str]] = set()
    scraped_bookings_map: Dict[Tuple[str, str, str],
                               str] = {}  # Map to store original case
    for booking in scraped_bookings:
        class_name: Optional[str] = booking.get('name')
        class_date_raw: Optional[str] = booking.get('date')
        class_time: Optional[str] = booking.get('time')

        if class_name and class_date_raw and class_time:
            try:
                date_part = ' '.join(class_date_raw.split(' ')[1:])
                date_part = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_part)
                current_year: int = datetime.now().year
                parsed_date: datetime = datetime.strptime(
                    f"{date_part} {current_year}", "%d %B %Y")
                class_date: str = parsed_date.strftime("%Y-%m-%d")
                key = (class_name.lower(), class_date, class_time)
                scraped_bookings_set.add(key)
                # Store original class name
                scraped_bookings_map[key] = class_name
            except Exception as e:
                logging.error(
                    f"Error parsing date '{class_date_raw}' during sync: {e}")
                continue

    # 3. Find bookings to add, to delete, and to check for case changes
    bookings_to_add: set[Tuple[str, str, str]
                         ] = scraped_bookings_set - db_bookings
    bookings_to_delete: set[Tuple[str, str, str]
                            ] = db_bookings - scraped_bookings_set
    bookings_to_check: set[Tuple[str, str, str]
                           ] = db_bookings.intersection(scraped_bookings_set)

    # 4. Check for case changes in existing bookings
    for key in bookings_to_check:
        scraped_name: str = scraped_bookings_map[key]
        db_info: Dict[str, Any] = db_bookings_map[key]
        db_name: str = db_info['name']

        if scraped_name != db_name:
            booking_id: int = db_info['id']
            database.update_live_booking_name(booking_id, scraped_name)
            logging.info(
                f"Updated class name case for booking ID {booking_id} from '{db_name}' to '{scraped_name}'.")

    # 5. Add new bookings
    for key in bookings_to_add:
        class_name_lower, class_date, class_time = key
        class_name_original: str = scraped_bookings_map[key]

        # Find the full booking details from the original scraped list
        full_booking: Optional[Dict[str, Any]] = next((b for b in scraped_bookings if b.get(
            'name', '').lower() == class_name_lower and b.get('time') == class_time), None)
        instructor: Optional[str] = full_booking.get(
            'instructor') if full_booking else None

        if not database.live_booking_exists(username, class_name_original, class_date, class_time):
            database.add_live_booking(
                username, class_name_original, class_date, class_time, instructor)
            logging.info(
                f"Added live booking for {username}: {class_name_original} on {class_date} at {class_time} to database.")

    # 6. Delete old bookings
    for key in bookings_to_delete:
        class_name_lower, class_date, class_time = key
        class_name_original: str = db_bookings_map[key]['name']
        database.delete_live_booking(
            username, class_name_original, class_date, class_time)
        logging.info(
            f"Deleted stale live booking for {username}: {class_name_original} on {class_date} at {class_time} from database.")


@app.route('/api/static_classes', methods=['GET'])
def get_static_classes() -> Tuple[Any, int]:
    # This endpoint does not require authentication or a scraper instance
    STATIC_TIMETABLE_PATH: str = os.path.join(
        os.path.dirname(__file__), 'static_timetable.json')
    if os.path.exists(STATIC_TIMETABLE_PATH):
        with open(STATIC_TIMETABLE_PATH, 'r') as f:
            static_classes_data: Dict[str, Any] = json.load(f)
        return jsonify(static_classes_data), 200
    else:
        logging.warning(
            f"Static timetable file not found at {STATIC_TIMETABLE_PATH}")
        return jsonify({"error": "Static timetable not found."}), 404


@app.route('/api/schedule_auto_book', methods=['POST'])
@jwt_required()
def schedule_auto_book() -> Tuple[Any, int]:
    current_user: str = get_jwt_identity()  # type: ignore
    data: Dict[str, Any] = request.get_json()  # type: ignore
    class_name: Optional[str] = data.get('class_name')
    target_time_str: Optional[str] = data.get('time')
    day_of_week: Optional[str] = data.get('day_of_week')
    instructor: Optional[str] = data.get('instructor')

    if not all([class_name, target_time_str, day_of_week, instructor]):
        return jsonify({"error": "class_name, time, day_of_week, and instructor are required."}), 400

    try:
        booking_id: int = database.add_auto_booking(
            current_user, class_name, target_time_str, day_of_week, instructor  # type: ignore
        )
        logging.info(
            f"Recurring auto-booking scheduled for user {current_user}: Class {class_name} "
            f"on {day_of_week} at {target_time_str}. Booking ID: {booking_id}")
        return jsonify({"message": "Recurring auto-booking scheduled successfully!", "booking_id": booking_id}), 201
    except Exception as e:
        logging.error(
            f"Error scheduling auto-booking for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred. Contact Administrator."}), 500


@app.route('/api/auto_bookings', methods=['GET'])
@jwt_required()
def get_auto_bookings() -> Tuple[Any, int]:
    current_user: str = get_jwt_identity()  # type: ignore
    try:
        bookings: List[Tuple] = database.get_auto_bookings_for_user(
            current_user)
        booking_list: List[Dict[str, Any]] = []
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
                "instructor": b[9] or "",
                "last_booked_date": b[10] or ""
            })
        return jsonify(booking_list), 200
    except Exception as e:
        logging.error(
            f"Error retrieving auto-bookings for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500


@app.route('/api/cancel_auto_book', methods=['POST'])
@jwt_required()
def cancel_auto_book() -> Tuple[Any, int]:
    current_user: str = get_jwt_identity()  # type: ignore
    data: Dict[str, Any] = request.get_json()  # type: ignore
    booking_id: Optional[int] = data.get('booking_id')

    if not booking_id:
        return jsonify({"error": "booking_id is required."}), 400

    try:
        if database.cancel_auto_booking(booking_id, current_user):
            logging.info(
                f"Auto-booking ID {booking_id} cancelled by user {current_user}.")
            return jsonify({"message": "Recurring auto-booking cancelled successfully!"}), 200
        else:
            return jsonify({"error": "Booking not found or not authorized to cancel. Contact Administrator"}), 404
    except Exception as e:
        logging.error(
            f"Error cancelling auto-booking ID {booking_id} for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500


@app.route('/api/vapid-public-key', methods=['GET'])
def get_vapid_public_key() -> Tuple[str, int]:
    return config.VAPID_PUBLIC_KEY, 200  # type: ignore


@app.route('/api/subscribe-push', methods=['POST'])
@jwt_required()
def subscribe_push() -> Tuple[Any, int]:
    current_user: str = get_jwt_identity()  # type: ignore
    subscription_info: Dict[str, Any] = request.get_json()  # type: ignore

    if not subscription_info:
        return jsonify({"error": "Subscription info is required."}), 400

    try:
        database.save_push_subscription(current_user, subscription_info)
        logging.info(f"Push subscription saved for user: {current_user}")
        return jsonify({"message": "Push subscription successful"}), 201
    except Exception as e:
        logging.error(
            f"Error saving push subscription for user {current_user}: {e}")
        return jsonify({"error": "Failed to save push subscription."}), 500


@app.route('/api/health', methods=['GET'])
def health_check() -> Tuple[Any, int]:
    """
    Returns the application's status and uptime.
    """
    uptime: timedelta = datetime.now() - app.start_time
    return jsonify({
        "status": "ok",
        "uptime": str(uptime)
    }), 200

# --- Admin Endpoints ---


@app.route('/api/admin/logs', methods=['GET'])
@admin_required
def get_logs() -> Tuple[Any, int]:
    log_pattern: re.Pattern[str] = re.compile(r'^(\S+ \S+) - (\w+) - (.*)')
    try:
        with open(LOG_FILE, 'r') as f:
            lines: List[str] = f.readlines()
            parsed_logs: List[Dict[str, str]] = []
            for line in reversed(lines[-100:]):
                match: Optional[re.Match[str]
                                ] = log_pattern.match(line.strip())
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
            return jsonify({"logs": parsed_logs}), 200
    except FileNotFoundError:
        return jsonify({"error": "Log file not found."}), 404


@app.route('/api/admin/auto_bookings', methods=['GET'])
@admin_required
def get_all_auto_bookings() -> Tuple[Any, int]:
    bookings_raw: List[Dict[str, Any]] = database.get_all_auto_bookings()
    bookings_formatted: List[Dict[str, Any]] = []
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
            "instructor": b["instructor"] or "",
            "last_booked_date": b["last_booked_date"] or ""
        })
    return jsonify(bookings_formatted), 200


@app.route('/api/admin/live_bookings', methods=['GET'])
@admin_required
def get_all_live_bookings() -> Tuple[Any, int]:
    bookings: List[Dict[str, Any]] = database.get_all_live_bookings()
    return jsonify(bookings), 200


@app.route('/api/admin/push_subscriptions', methods=['GET'])
@admin_required
def get_all_push_subscriptions() -> Tuple[Any, int]:
    subscriptions: List[Dict[str, Any]] = database.get_all_push_subscriptions()
    return jsonify(subscriptions), 200


@app.route('/api/admin/sessions', methods=['GET'])
@admin_required
def get_all_sessions() -> Tuple[Any, int]:
    sessions: List[Dict[str, Any]] = database.get_all_sessions()
    return jsonify(sessions), 200


@app.route('/api/admin/status', methods=['GET'])
@admin_required
def get_status() -> Tuple[Any, int]:
    uptime: timedelta = datetime.now() - app.start_time
    ssh_command: Optional[str] = None
    try:
        tunnels_response: requests.Response = requests.get(
            'http://127.0.0.1:4040/api/tunnels')
        tunnels_response.raise_for_status()
        tunnels_data: Dict[str, Any] = tunnels_response.json()
        for tunnel in tunnels_data.get('tunnels', []):
            if tunnel.get('proto') == 'tcp':
                public_url: Optional[str] = tunnel.get('public_url')
                if public_url:
                    # Extract host and port
                    match: Optional[re.Match[str]] = re.match(
                        r'tcp://(.+):(\d+)', public_url)
                    if match:
                        host: str = match.group(1)
                        port: str = match.group(2)
                        ssh_command = f"ssh -p {port} gabs-admin@{host}"
                break
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch ngrok tunnels: {e}")

    return jsonify({
        "status": "ok",
        "uptime": str(uptime),
        "ssh_tunnel_command": ssh_command
    }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
