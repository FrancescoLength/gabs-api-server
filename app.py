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

from scraper import Scraper
import config
import database
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

            # Calculate the next target date for this recurring booking
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

            user_scraper = scraper_cache.get(username)
            if not user_scraper:
                logging.warning(f"Scraper session for {username} not found for auto-booking {booking_id}. Re-login required.")
                database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()))
                continue

            try:
                logging.info(f"Attempting to book class on {current_target_date} at {target_time} with {instructor} for user {username} (Booking ID: {booking_id})")
                result = user_scraper.find_and_book_class(target_date_str=current_target_date, class_name=class_name, target_time=target_time, instructor=instructor)
                
                result_message = result.get('message', '').lower()
                if result.get('status') == 'success' or (result.get('status') == 'info' and ("already registered" in result_message or "waiting list" in result_message or "already booked" in result_message)):
                    database.update_auto_booking_status(booking_id, 'pending', last_booked_date=current_target_date, last_attempt_at=int(datetime.now().timestamp()))
                    logging.info(f"Successfully processed booking for auto-booking {booking_id}. Status: {result.get('message')}")
                else:
                    new_retry_count = retry_count + 1
                    if new_retry_count < config.MAX_AUTO_BOOK_RETRIES:
                        database.update_auto_booking_status(booking_id, 'pending', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.warning(f"Booking attempt failed for auto-booking {booking_id}. Retrying (attempt {new_retry_count}). Result: {result}")
                    else:
                        database.update_auto_booking_status(booking_id, 'failed', last_attempt_at=int(datetime.now().timestamp()), retry_count=new_retry_count)
                        logging.error(f"Booking attempt failed for auto-booking {booking_id} after {new_retry_count} retries. Marking as failed. Result: {result}")
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
        upcoming_bookings = database.get_upcoming_bookings_for_notification()

        for booking in upcoming_bookings:
            booking_id, username, class_name, target_time_str, day_of_week, instructor, last_booked_date, notification_sent = booking

            # Calculate the next occurrence date for this recurring booking
            today = datetime.now()
            days_of_week_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
            target_day_index = days_of_week_map[day_of_week]

            days_until_target = (target_day_index - today.weekday() + 7) % 7
            next_occurrence_date = today + timedelta(days=days_until_target)
            current_target_date = next_occurrence_date.strftime("%Y-%m-%d")

            # Combine date and time to create a full datetime object for the class
            class_datetime_str = f"{current_target_date} {target_time_str}"
            class_datetime = datetime.strptime(class_datetime_str, "%Y-%m-%d %H:%M")

            # If the calculated class time is in the past, it's an expired booking.
            if class_datetime < datetime.now():
                # Check if the last_booked_date is today. If so, the booking has been processed.
                # If not, it's a missed booking.
                if last_booked_date != current_target_date:
                    logging.warning(f"Auto-booking {booking_id} for {class_name} on {current_target_date} at {target_time_str} was missed.")
                    # Mark as expired by updating the last_booked_date. This will prevent it from being picked up again.
                    database.update_auto_booking_status(booking_id, 'pending', last_booked_date=current_target_date)
                # In either case, we skip the reminder.
                continue

            # If last_booked_date is set, ensure we are looking at the correct occurrence
            if last_booked_date and last_booked_date == current_target_date:
                # This means we already processed this date, skip to avoid re-processing
                continue

            cancellation_deadline = class_datetime - timedelta(minutes=180)
            notification_time = cancellation_deadline - timedelta(minutes=30)

            if notification_time <= datetime.now() < cancellation_deadline:
                logging.info(f"Sending cancellation reminder for booking {booking_id} for user {username}")
                subscriptions = database.get_push_subscriptions_for_user(username)

                for sub in subscriptions:
                    try:
                        webpush(
                            subscription_info=sub,
                            data=json.dumps({
                                "title": "Class Cancellation Reminder!",
                                "body": f"Delete {class_name} at {target_time_str} within 30 min to avoid a fine!",
                                "icon": "/favicon.png",
                                "badge": "/favicon.png",
                                "tag": f"cancellation-reminder-{booking_id}",
                                "url": "/my-bookings"
                            }),
                            vapid_private_key=config.VAPID_PRIVATE_KEY,
                            vapid_claims={"sub": config.VAPID_ADMIN_EMAIL}
                        )
                        logging.info(f"Push notification sent for booking {booking_id} to user {username}")
                    except Exception as e:
                        logging.error(f"Error sending push notification to {sub['endpoint']} for user {username}: {e}")
                        if "410" in str(e):
                            database.delete_push_subscription(sub['endpoint'])
                            logging.info(f"Deleted invalid push subscription for user {username}: {sub['endpoint']}")

                database.update_auto_booking_status(booking_id, 'pending', notification_sent=1)
            elif datetime.now() >= cancellation_deadline and notification_sent == 0:
                database.update_auto_booking_status(booking_id, 'pending', notification_sent=1)

def reset_failed_bookings():
    with app.app_context():
        failed_bookings = database.get_failed_auto_bookings()
        for booking in failed_bookings:
            booking_id, last_attempt_at = booking
            if last_attempt_at:
                # Reset if the last attempt was more than 24 hours ago
                if (datetime.now().timestamp() - last_attempt_at) > (24 * 60 * 60):
                    logging.info(f"Resetting failed auto-booking {booking_id} to 'pending'.")
                    database.update_auto_booking_status(booking_id, 'pending', retry_count=0)

# The jobs are now added and started by the standalone scheduler_runner.py process
# scheduler.add_job(process_auto_bookings, 'interval', minutes=1, id='auto_booking_processor', replace_existing=True)
# scheduler.add_job(send_cancellation_reminders, 'interval', minutes=1, id='cancellation_reminder_sender', replace_existing=True)
# scheduler.add_job(reset_failed_bookings, 'interval', hours=24, id='reset_failed_bookings_job', replace_existing=True)
# scheduler.start()
# logging.info("APScheduler started.")

# import atexit
# atexit.register(lambda: scheduler.shutdown())

app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
app.config["JWT_TOKEN_LOCATION"] = ["headers"]
jwt = JWTManager(app)

# --- Admin decorator ---
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        current_user = get_jwt_identity()
        if current_user != config.ADMIN_EMAIL:
            return jsonify({"error": "Admins only!"}), 403
        return fn(*args, **kwargs)
    return wrapper

scraper_cache = {}

STATIC_TIMETABLE_PATH = os.path.join(os.path.dirname(__file__), 'static_timetable.json')
static_classes_data = {}
if os.path.exists(STATIC_TIMETABLE_PATH):
    with open(STATIC_TIMETABLE_PATH, 'r') as f:
        static_classes_data = json.load(f)
    logging.info("Static timetable loaded successfully.")
else:
    logging.warning(f"Static timetable file not found at {STATIC_TIMETABLE_PATH}")

@app.route('/api/static_classes', methods=['GET'])
def get_static_classes():
    return jsonify(static_classes_data)

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
        return jsonify({"error": "class_name, time, day_of_week, and instructor are required."}), 400

    try:
        booking_id = database.add_auto_booking(
            current_user, class_name, target_time_str, day_of_week, instructor
        )
        logging.info(f"Recurring auto-booking scheduled for user {current_user}: Class {class_name} on {day_of_week} at {target_time_str}. Booking ID: {booking_id}")
        return jsonify({"message": "Recurring auto-booking scheduled successfully", "booking_id": booking_id}), 201
    except Exception as e:
        logging.error(f"Error scheduling auto-booking for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

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
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/cancel_auto_book', methods=['POST'])
@jwt_required()
def cancel_auto_book():
    current_user = get_jwt_identity()
    data = request.get_json()
    booking_id = data.get('booking_id')

    if not booking_id:
        return jsonify({"error": "booking_id is required."}), 400

    try:
        if database.cancel_auto_booking(booking_id, current_user):
            logging.info(f"Auto-booking ID {booking_id} cancelled by user {current_user}.")
            return jsonify({"message": f"Auto-booking ID {booking_id} cancelled successfully."}), 200
        else:
            return jsonify({"error": "Booking not found or not authorized to cancel."}), 404
    except Exception as e:
        logging.error(f"Error cancelling auto-booking ID {booking_id} for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/login', methods=['POST'])
def login_user():
    data = request.get_json()
    username = data.get('username', None)
    password = data.get('password', None)

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    try:
        logging.info(f"Login attempt for user: {username}")
        user_scraper = Scraper(username, password)
        scraper_cache[username] = user_scraper
        access_token = create_access_token(identity=username)
        logging.info(f"Successfully created session and token for {username}")
        return jsonify(access_token=access_token)
    except Exception as e:
        logging.error(f"Failed login for user {username}: {e}")
        return jsonify({"error": "Invalid credentials or login failed"}), 401

@app.route('/api/classes', methods=['GET'])
@jwt_required()
def get_available_classes():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    try:
        classes = user_scraper.get_classes(days_in_advance=7)
        return jsonify(classes)
    except Exception as e:
        logging.error(f"Error in /api/classes for user {current_user}: {e}")
        return jsonify({"error": "Failed to retrieve classes."}), 500

@app.route('/api/book', methods=['POST'])
@jwt_required()
def book_class():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date')
    target_time = data.get('time')  # Extract time from request
    if not all([class_name, target_date, target_time]):
        return jsonify({"error": "class_name, date, and time are required."}), 400
    
    logging.info(f"User {current_user} attempting to book class {class_name} on {target_date} at {target_time}")
    try:
        # Pass time to the scraper function
        result = user_scraper.find_and_book_class(
            target_date_str=target_date, 
            class_name=class_name, 
            target_time=target_time
        )
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Unhandled error in /api/book for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/cancel', methods=['POST'])
@jwt_required()
def cancel_booking():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date')
    target_time = data.get('time')
    if not class_name or not target_date or not target_time:
        return jsonify({"error": "class_name, date, and time are required."}), 400
    
    logging.info(f"User {current_user} attempting to cancel class {class_name} on {target_date} at {target_time}")
    try:
        result = user_scraper.find_and_cancel_booking(class_name, target_date, target_time)
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Unhandled error in /api/cancel for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/bookings', methods=['GET'])
@jwt_required()
def get_my_bookings():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    try:
        bookings = user_scraper.get_my_bookings()
        return jsonify(bookings)
    except Exception as e:
        logging.error(f"Unhandled error in /api/bookings for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/availability', methods=['GET'])
@jwt_required()
def get_availability():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    class_name = request.args.get('class_name')
    target_date = request.args.get('date')
    if not class_name or not target_date:
        return jsonify({"error": "class_name and date are required."}), 400
    try:
        result = user_scraper.get_class_availability(class_name, target_date)
        return jsonify(result)
    except Exception as e:
        logging.error(f"Unhandled error in /api/availability for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/instructors', methods=['GET'])
@jwt_required()
def get_instructors():
    current_user = get_jwt_identity()
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    try:
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
    except Exception as e:
        logging.error(f"Unhandled error in /api/instructors for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/classes-by-instructor', methods=['GET'])
@jwt_required()
def get_classes_by_instructor():
    current_user = get_jwt_identity()
    instructor_name_query = request.args.get('name')
    if not instructor_name_query:
        return jsonify({"error": "'name' query parameter is required."}), 400
    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    try:
        classes = user_scraper.get_classes(days_in_advance=7)
        instructor_classes = []
        for a_class in classes:
            instructor_name = a_class.get('instructor')
            if instructor_name and instructor_name_query.lower() in instructor_name.lower():
                instructor_classes.append(a_class)
        return jsonify(instructor_classes)
    except Exception as e:
        logging.error(f"Unhandled error in /api/classes-by-instructor for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route("/api/logout", methods=["POST"])
@jwt_required()
def logout_user():
    current_user = get_jwt_identity()
    if current_user in scraper_cache:
        del scraper_cache[current_user]
        logging.info(f"Removed session for user: {current_user}")
    return jsonify({"message": "Successfully logged out"})

@app.route('/api/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    return config.VAPID_PUBLIC_KEY, 200

@app.route('/api/subscribe-push', methods=['POST'])
@jwt_required()
def subscribe_push():
    current_user = get_jwt_identity()
    subscription_info = request.get_json()

    if not subscription_info:
        return jsonify({"error": "Subscription info is required."}), 400

    try:
        # This function will be implemented in database.py in the next step
        database.save_push_subscription(current_user, subscription_info)
        logging.info(f"Push subscription saved for user: {current_user}")
        return jsonify({"message": "Push subscription successful"}), 201
    except Exception as e:
        logging.error(f"Error saving push subscription for user {current_user}: {e}")
        return jsonify({"error": "Failed to save push subscription."}), 500

@app.route('/api/test-push-notification', methods=['POST'])
@jwt_required()
def test_push_notification():
    current_user = get_jwt_identity()
    logging.info(f"User {current_user} is requesting to send a test push notification.")

    try:
        all_subscriptions = database.get_all_push_subscriptions()
        if not all_subscriptions:
            return jsonify({"message": "No push subscriptions found."}), 200

        sent_count = 0
        failed_count = 0
        for sub in all_subscriptions:
            try:
                # Use a generic test message
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
                if "410" in str(e): # GONE status code for invalid subscription
                    database.delete_push_subscription(sub['endpoint'])
                    logging.info(f"Deleted invalid push subscription for user {sub['username']}: {sub['endpoint']}")
                failed_count += 1
        
        return jsonify({"message": f"Test push notifications sent. Successful: {sent_count}, Failed: {failed_count}."}), 200
    except Exception as e:
        logging.error(f"Error in test_push_notification endpoint: {e}")
        return jsonify({"error": "An internal server error occurred during test notification."}), 500

# --- Admin Endpoints ---

@app.route('/api/admin/logs', methods=['GET'])
@admin_required
def get_logs():
    # Regex to parse log lines: captures timestamp, level, and message
    log_pattern = re.compile(r'^(\S+ \S+) - (\w+) - (.*)')
    
    try:
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
            parsed_logs = []
            for line in reversed(lines[-100:]): # Reverse to show newest first
                match = log_pattern.match(line.strip())
                if match:
                    parsed_logs.append({
                        "timestamp": match.group(1),
                        "level": match.group(2),
                        "message": match.group(3)
                    })
                elif line.strip(): # Handle lines that don't match (e.g., tracebacks)
                    # Append to the previous message if it exists, else create a new entry
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
        return jsonify({"error": "Log file not found."}), 404

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
