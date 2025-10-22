import json
import os
import re
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler

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

# --- Session Management ---
scraper_cache = {}

def get_scraper_instance(username, password=None):
    """Gets a scraper instance for a user, from cache, database, or by creating a new one."""
    if username in scraper_cache:
        return scraper_cache[username]

    encrypted_password, session_data = database.load_session(username)
    
    if encrypted_password:
        # If a password is provided, it means we are in a login flow, so we use it.
        # Otherwise, we decrypt the one from the database for session restoration.
        password_to_use = password if password else crypto.decrypt(encrypted_password)
        
        try:
            scraper = Scraper(username, password_to_use, session_data=session_data)
            scraper_cache[username] = scraper
            # Persist the potentially updated session data from the scraper
            database.save_session(username, encrypted_password, scraper.to_dict())
            return scraper
        except Exception as e:
            logging.error(f"Failed to create or restore session for {username}: {e}")
            return None
    
    if password:
        try:
            scraper = Scraper(username, password)
            scraper_cache[username] = scraper
            encrypted_pass = crypto.encrypt(password)
            database.save_session(username, encrypted_pass, scraper.to_dict())
            return scraper
        except Exception as e:
            logging.error(f"Failed to create new session for {username}: {e}")
            return None

    return None

def handle_session_expiration(username):
    """Handles session expiration by creating a new scraper instance, forcing re-login."""
    logging.warning(f"Session expired for {username}. Forcing re-login.")
    if username in scraper_cache:
        del scraper_cache[username] # Remove expired instance
    
    encrypted_password, _ = database.load_session(username)
    if not encrypted_password:
        raise Exception("No credentials stored for user, cannot re-login.")

    password = crypto.decrypt(encrypted_password)
    scraper = Scraper(username, password)
    scraper_cache[username] = scraper
    database.save_session(username, encrypted_password, scraper.to_dict())
    return scraper

# --- APScheduler Configuration ---
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}')
}
scheduler = BackgroundScheduler(jobstores=jobstores)

def process_auto_bookings():
    with app.app_context():
        pending_bookings = database.get_pending_auto_bookings()
        for booking in pending_bookings:
            booking_id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date, notification_sent = booking

            today = datetime.now()
            days_of_week_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
            target_day_index = days_of_week_map[day_of_week]
            
            days_until_target = (target_day_index - today.weekday() + 7) % 7
            next_occurrence_date = today + timedelta(days=days_until_target)
            current_target_date = next_occurrence_date.strftime("%Y-%m-%d")

            if last_booked_date == current_target_date:
                continue

            target_datetime = datetime.strptime(f"{current_target_date} {target_time}", "%Y-%m-%d %H:%M")
            booking_time = int((target_datetime - timedelta(hours=48)).timestamp())

            if booking_time > int(datetime.now().timestamp()):
                continue
            
            retry_count = 0

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
                    database.update_auto_booking_status(booking_id, 'pending', last_booked_date=current_target_date, last_attempt_at=int(datetime.now().timestamp()))
                    database.add_live_booking(username, class_name, current_target_date, target_time, instructor, booking_id)
                    logging.info(f"Successfully processed booking for auto-booking {booking_id}. Status: {result.get('message')}")
                else:
                    new_retry_count = retry_count + 1
                    if new_retry_count < config.MAX_AUTO_BOOK_RETRIES:
                        database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.warning(f"Booking attempt failed for auto-booking {booking_id}. Retrying (attempt {new_retry_count}). Result: {result}")
                    else:
                        database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.error(f"Booking attempt failed for auto-booking {booking_id} after {new_retry_count} retries. Marking as failed. Result: {result}")
            except SessionExpiredError:
                handle_session_expiration(username)
                logging.warning(f"Session expired for {username} during auto-booking. Re-logged in, will retry on next cycle.")
            except Exception as e:
                new_retry_count = retry_count + 1
                if new_retry_count < config.MAX_AUTO_BOOK_RETRIES:
                    database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                    logging.error(f"Error during booking attempt for auto-booking {booking_id}: {e}. Retrying (attempt {new_retry_count}).")
                else:
                    database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                    logging.error(f"Error during booking attempt for auto-booking {booking_id}: {e}. Marking as failed after {new_retry_count} retries.")

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
            if timedelta(hours=3, minutes=29) <= time_until_class <= timedelta(hours=3, minutes=31):
                logging.info(f"Sending cancellation reminder for live booking ID {booking_id} for user {username}.")
                subscriptions = database.get_push_subscriptions_for_user(username)
                
                if subscriptions:
                    for sub in subscriptions:
                        try:
                            webpush(
                                subscription_info=sub,
                                data=json.dumps({
                                    "title": "Reminder: Cancel Your Class!",
                                    "body": f"Don't forget to cancel your {class_name} class on {class_date_str} at {class_time_str} if you can't make it!",
                                    "icon": "/favicon.png",
                                    "badge": "/favicon.png",
                                    "tag": f"cancellation-reminder-{booking_id}",
                                    "url": "/my-bookings"
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
                    logging.info(f"No push subscriptions found for user {username} for live booking ID {booking_id}.")
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

# ... (rest of the scheduler functions remain the same for now)

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
    if current_user in scraper_cache:
        del scraper_cache[current_user]
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
            try:
                user_scraper = handle_session_expiration(current_user)
                # Retry the original function call with the new scraper instance
                return f(user_scraper, *args, **kwargs)
            except Exception as e:
                logging.error(f"Failed to re-authenticate user {current_user} after session expiration: {e}")
                return jsonify({"error": "Your session expired and could not be refreshed. Please log in again."}), 401
        except Exception as e:
            logging.error(f"Unhandled error in scraper endpoint for user {current_user}: {e}")
            return jsonify({"error": "An internal server error occurred."}), 500
    return decorated_function

@app.route('/api/classes', methods=['GET'])
@scraper_endpoint
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

# ... (all other scraper-dependent endpoints need to be wrapped with @scraper_endpoint)

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
    return jsonify(result), 200

@app.route('/api/bookings', methods=['GET'])
@scraper_endpoint
def get_my_bookings(user_scraper):
    current_user = get_jwt_identity()
    bookings = user_scraper.get_my_bookings()
    
    for booking in bookings:
        class_name = booking.get('name')
        class_date_raw = booking.get('date')
        class_time = booking.get('time')
        instructor = booking.get('instructor')

        if class_name and class_date_raw and class_time:
            # Parse the date from the scraper (e.g., 'Tuesday 21st October') to 'YYYY-MM-DD'
            try:
                # Example: 'Tuesday 21st October' -> '21 October'
                date_part = ' '.join(class_date_raw.split(' ')[1:]) 
                # Remove 'st', 'nd', 'rd', 'th' from day
                date_part = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_part)
                # Add current year for parsing
                current_year = datetime.now().year
                parsed_date = datetime.strptime(f"{date_part} {current_year}", "%d %B %Y")
                class_date = parsed_date.strftime("%Y-%m-%d")
            except Exception as e:
                logging.error(f"Error parsing date '{class_date_raw}': {e}")
                continue # Skip this booking if date parsing fails

            if not database.live_booking_exists(current_user, class_name, class_date, class_time):
                database.add_live_booking(current_user, class_name, class_date, class_time, instructor)
                logging.info(f"Added live booking for {current_user}: {class_name} on {class_date} at {class_time} to database.")

    return jsonify(bookings)

@app.route('/api/availability', methods=['GET'])
@scraper_endpoint
def get_availability(user_scraper):
    class_name = request.args.get('class_name')
    target_date = request.args.get('date')
    if not class_name or not target_date:
        return jsonify({"error": "class_name and date are required."} ), 400
    result = user_scraper.get_class_availability(class_name, target_date)
    return jsonify(result)

@app.route('/api/instructors', methods=['GET'])
@scraper_endpoint
def get_instructors(user_scraper):
    classes = user_scraper.get_classes(days_in_advance=7)
    instructors = {}
    for a_class in classes:
        instructor_name = a_class.get('instructor')
        if instructor_name and instructor_name != "N/A":
            if instructor_name not in instructors:
                instructors[instructor_name] = []
            instructors[instructor_name].append({
                'name': a_class['name'],
                'date': a_class['date'],
                'time': a_class['time']
            })
    return jsonify(instructors)

@app.route('/api/classes-by-instructor', methods=['GET'])
@scraper_endpoint
def get_classes_by_instructor(user_scraper):
    instructor_name_query = request.args.get('name')
    if not instructor_name_query:
        return jsonify({"error": "'name' query parameter is required."} ), 400
    classes = user_scraper.get_classes(days_in_advance=7)
    instructor_classes = []
    for a_class in classes:
        instructor_name = a_class.get('instructor')
        if instructor_name and instructor_name_query.lower() in instructor_name.lower():
            instructor_classes.append(a_class)
    return jsonify(instructor_classes)

# --- Unchanged endpoints ---

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
        return jsonify({"message": "Recurring auto-booking scheduled successfully", "booking_id": booking_id}), 201
    except Exception as e:
        logging.error(f"Error scheduling auto-booking for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."} ), 500

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
            return jsonify({"message": f"Auto-booking ID {booking_id} cancelled successfully."} ), 200
        else:
            return jsonify({"error": "Booking not found or not authorized to cancel."} ), 404
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
        database.save_push_subscription(current_user, subscription_info)
        logging.info(f"Push subscription saved for user: {current_user}")
        return jsonify({"message": "Push subscription successful"}), 201
    except Exception as e:
        logging.error(f"Error saving push subscription for user {current_user}: {e}")
        return jsonify({"error": "Failed to save push subscription."} ), 500

@app.route('/api/test-push-notification', methods=['POST'])
@jwt_required()
def test_push_notification():
    current_user = get_jwt_identity()
    logging.info(f"User {current_user} is requesting to send a test push notification.")

    try:
        all_subscriptions = database.get_all_push_subscriptions()
        if not all_subscriptions:
            return jsonify({"message": "No push subscriptions found."} ), 200

        sent_count = 0
        failed_count = 0
        for sub in all_subscriptions:
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": "Test Notifica Push!",
                        "body": f"Ciao {sub['username']}! Questa  una notifica di test dal tuo backend GABS.",
                        "icon": "/favicon.png",
                        "badge": "/favicon.png",
                        "tag": "test-notification",
                        "url": "/my-bookings"
                    }),
                    vapid_private_key=config.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": config.VAPID_ADMIN_EMAIL}
                )
                logging.info(f"Test push notification sent to {sub['username']} ({sub['endpoint']})")
                sent_count += 1
            except Exception as e:
                logging.error(f"Error sending test push notification to {sub['username']} ({sub['endpoint']}): {e}")
                if "410" in str(e):
                    database.delete_push_subscription(sub['endpoint'])
                    logging.info(f"Deleted invalid push subscription for user {sub['username']}: {sub['endpoint']}")
                failed_count += 1
        
        return jsonify({"message": f"Test push notifications sent. Successful: {sent_count}, Failed: {failed_count}."}), 200
    except Exception as e:
        logging.error(f"Error in test_push_notification endpoint: {e}")
        return jsonify({"error": "An internal server error occurred during test notification."} ), 500

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
            "last_booked_date": b[10],
            "notification_sent": b[11]
        })
    return jsonify(bookings_formatted)

@app.route('/api/admin/push_subscriptions', methods=['GET'])
@admin_required
def get_all_push_subscriptions():
    subscriptions = database.get_all_push_subscriptions()
    return jsonify(subscriptions)

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def get_all_users():
    users = database.get_all_users()
    return jsonify(users)

@app.route('/api/admin/sessions', methods=['GET'])
@admin_required
def get_all_sessions():
    sessions = database.get_all_sessions()
    return jsonify(sessions)

@app.route('/api/admin/status', methods=['GET'])
@admin_required
def get_status():
    uptime = datetime.now() - app.start_time
    return jsonify({
        "status": "ok",
        "uptime": str(uptime),
        "scraper_cache_size": len(scraper_cache)
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)