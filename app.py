from flask import Flask, jsonify, request
from flask_cors import CORS
import logging
from datetime import timedelta

from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager

from scraper import Scraper
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
# Enable CORS for all routes, allowing your React app to make requests
CORS(app)

# --- JWT Configuration ---
app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24) # Set token expiration
jwt = JWTManager(app)

# --- Scraper Cache ---
# This dictionary will hold active, logged-in scraper instances.
# Key: username, Value: Scraper instance
scraper_cache = {}

# --- API Endpoints ---

@app.route('/', methods=['GET'])
def index():
    return "Gym Booking API is running. Use /api/login to authenticate."

@app.route('/api/login', methods=['POST'])
def login_user():
    """Authenticates a user against the gym website and returns a JWT."""
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
    """Returns a JSON list of all available classes for the next 7 days."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/classes received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    try:
        classes = user_scraper.get_classes(days_in_advance=7)
        return jsonify(classes)
    except Exception as e:
        logging.error(f"Error in /api/classes for user {current_user}: {e}")
        return jsonify({"error": "Failed to retrieve classes. Your session may have expired. Please log in again."}), 500

@app.route('/api/book', methods=['POST'])
@jwt_required()
def book_class():
    """Books a class for the logged-in user."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/book received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date') # Expected format: YYYY-MM-DD

    if not class_name or not target_date:
        return jsonify({"error": "class_name and date (YYYY-MM-DD) are required."}), 400

    try:
        result = user_scraper.find_and_book_class(class_name, target_date)
        if result.get('status') == 'error':
            return jsonify(result), 400
        if result.get('status') == 'info':
            return jsonify(result), 200
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Unhandled error in /api/book for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/cancel', methods=['POST'])
@jwt_required()
def cancel_booking():
    """Cancels a class booking for the logged-in user."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/cancel received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    data = request.get_json()
    class_name = data.get('class_name')
    target_date = data.get('date') # Expected format: YYYY-MM-DD

    if not class_name or not target_date:
        return jsonify({"error": "class_name and date (YYYY-MM-DD) are required."}), 400

    try:
        result = user_scraper.find_and_cancel_booking(class_name, target_date)
        if result.get('status') == 'error':
            return jsonify(result), 400
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Unhandled error in /api/cancel for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/bookings', methods=['GET'])
@jwt_required()
def get_my_bookings():
    """Gets the list of all current bookings and waiting list entries for the user."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/bookings received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401
    
    try:
        bookings = user_scraper.get_my_bookings()
        if isinstance(bookings, dict) and bookings.get('error'):
            return jsonify(bookings), 500 # Propagate errors from the scraper
        return jsonify(bookings)
    except Exception as e:
        logging.error(f"Unhandled error in /api/bookings for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/availability', methods=['GET'])
@jwt_required()
def get_availability():
    """Gets the availability for a specific class."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/availability received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    # Get parameters from query string
    class_name = request.args.get('class_name')
    target_date = request.args.get('date') # Expected format: YYYY-MM-DD

    if not class_name or not target_date:
        return jsonify({"error": "class_name and date (YYYY-MM-DD) are required query parameters."}), 400

    try:
        result = user_scraper.get_class_availability(class_name, target_date)
        if result.get('error'):
            return jsonify(result), 404 # Use 404 for not found errors
        return jsonify(result)
    except Exception as e:
        logging.error(f"Unhandled error in /api/availability for user {current_user}: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

@app.route('/api/instructors', methods=['GET'])
@jwt_required()
def get_instructors():
    """Returns a dictionary of instructors and their classes for the next 7 days."""
    current_user = get_jwt_identity()
    logging.info(f"API call to /api/instructors received for user: {current_user}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    try:
        classes = user_scraper.get_classes(days_in_advance=7)
        if isinstance(classes, dict) and classes.get('error'):
            return jsonify(classes), 500

        instructors = {}
        for a_class in classes:
            instructor_name = a_class.get('instructor')
            if instructor_name and instructor_name != "N/A":
                if instructor_name not in instructors:
                    instructors[instructor_name] = []
                # Append a cleaner version of the class info
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
    """Returns a list of classes for a specific instructor."""
    current_user = get_jwt_identity()
    instructor_name_query = request.args.get('name')

    if not instructor_name_query:
        return jsonify({"error": "'name' query parameter is required."}), 400

    logging.info(f"API call to /api/classes-by-instructor for user: {current_user}, instructor: {instructor_name_query}")

    user_scraper = scraper_cache.get(current_user)
    if not user_scraper:
        return jsonify({"error": "Session not found. Please log in again."}), 401

    try:
        classes = user_scraper.get_classes(days_in_advance=7)
        if isinstance(classes, dict) and classes.get('error'):
            return jsonify(classes), 500

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
    """Logs a user out by removing their scraper instance from the cache."""
    current_user = get_jwt_identity()
    if current_user in scraper_cache:
        del scraper_cache[current_user]
        logging.info(f"Removed session for user: {current_user}")
    return jsonify({"message": "Successfully logged out"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)