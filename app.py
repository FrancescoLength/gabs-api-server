import json
import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
from datetime import datetime, timedelta

from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager

from scraper import Scraper
import config
import database

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app)

database.init_db()

# --- APScheduler Configuration ---
jobstores = {
    'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}')
}
scheduler = BackgroundScheduler(jobstores=jobstores)

def process_auto_bookings():
    with app.app_context():
        logging.info("Running scheduled job: process_auto_bookings")
        pending_bookings = database.get_pending_auto_bookings()
        for booking in pending_bookings:
            booking_id, username, class_name, target_time, status, created_at, last_attempt_at, retry_count, day_of_week, instructor, last_booked_date = booking

            # Calculate the next target date for this recurring booking
            today = datetime.now()
            days_of_week_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
            target_day_index = days_of_week_map[day_of_week]
            
            days_until_target = (target_day_index - today.weekday() + 7) % 7
            next_occurrence_date = today + timedelta(days=days_until_target)
            current_target_date = next_occurrence_date.strftime("%Y-%m-%d")

            if last_booked_date == current_target_date:
                logging.info(f"Recurring booking {booking_id} for {current_target_date} already processed. Skipping.")
                continue

            target_datetime = datetime.strptime(f"{current_target_date} {target_time}", "%Y-%m-%d %H:%M")
            booking_time = int((target_datetime - timedelta(hours=48)).timestamp())

            if booking_time > int(datetime.now().timestamp()) + 300: # 5 minutes grace period
                logging.info(f"Recurring booking {booking_id} for {current_target_date} is not yet due. Skipping.")
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
                
                if result.get('status') == 'success' or (result.get('status') == 'info' and "already booked" in result.get('message', '').lower()):
                    database.update_auto_booking_status(booking_id, 'pending', last_booked_date=current_target_date, last_attempt_at=int(datetime.now().timestamp()))
                    logging.info(f"Successfully processed booking for auto-booking {booking_id}.")
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

scheduler.add_job(process_auto_bookings, 'interval', minutes=1, id='auto_booking_processor', replace_existing=True)
scheduler.start()
logging.info("APScheduler started.")

import atexit
atexit.register(lambda: scheduler.shutdown())

app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
jwt = JWTManager(app)

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
