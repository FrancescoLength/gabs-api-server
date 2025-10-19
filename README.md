# GABS API Server: Automated Gym Booking & Management

This Flask-based API server empowers users to seamlessly interact with a famous Bristol gym's website, automating the process of viewing, booking, and managing gym class reservations. Designed for efficiency and convenience, it acts as a robust backend for custom client applications (such as a React frontend), ideal for deployment on low-power devices like a Raspberry Pi.

## Key Features

-   **Secure User Authentication:** Utilizes JWT for secure user login and session management.
-   **Comprehensive Class Overview:** Access a real-time list of available gym classes for the upcoming 7 days.
-   **Effortless Booking & Waitlisting:** Book classes directly or automatically join the waiting list if a class is full.
-   **Streamlined Cancellation:** Easily cancel existing bookings.
-   **Personalized Booking Management:** View all your upcoming class bookings and waiting list entries.
-   **Real-time Availability Checks:** Instantly check the number of remaining spaces for any specific class.
-   **Automated Recurring Bookings:** Schedule and manage recurring auto-bookings for your favorite classes. The scheduler runs in a dedicated process to ensure time-critical bookings are handled with high precision and concurrency.
-   **Intelligent Push Notifications:** Receive timely push notifications, including crucial cancellation reminders.
-   **Admin Panel:** Dedicated endpoints for administrators to monitor logs, auto-bookings, push subscriptions, and server status.

## Architecture Overview

The application is designed with a decoupled architecture to ensure stability and performance, especially for time-sensitive tasks.

-   **`app.py`**: The core Flask web application. Its sole responsibility is to handle incoming API requests from the client. It does not run any background jobs itself.
-   **`scheduler_runner.py`**: A standalone, dedicated process that runs the APScheduler for all background tasks. It uses a thread pool to execute jobs concurrently, which is critical for handling multiple auto-booking requests for the same class without delays.
-   **`logging_config.py`**: A centralized module that provides a consistent logging format for both the web server and the scheduler processes.
-   **`scraper.py`**: A robust web scraping client that handles all interactions with the gym's website, including advanced headers to mimic a real browser and prevent blocking.

## Setup and Installation

1.  **Prerequisites:**
    -   Python 3.8+
    -   Git

2.  **Clone the Repository:**
    ```bash
    git clone https://github.com/FrancescoLength/gabs-api-server.git
    cd gabs_api_server
    ```

3.  **Install Dependencies:**
    ```bash
    # (Recommended) Create and activate a virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install dependencies
    pip install -r requirements.txt
    ```

4.  **Configuration (.env file):**
    This project uses environment variables to manage sensitive information and configuration.
    -   Create a file named `.env` in the root of the `gabs_api_server` directory.
    -   Copy the content from `.env.example` and fill in the required values (e.g., `JWT_SECRET_KEY`, `VAPID_PRIVATE_KEY`, etc.).

5.  **Generate VAPID Keys (if needed):**
    If you need to generate new VAPID keys for push notifications, run the provided script:
    ```bash
    python generate_vapid_keys_manual.py
    ```
    Update your `.env` file with the generated `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`.

## Running the Server

### Development (Local Testing)

For local development, you need to run two separate processes in two different terminals.

1.  **Terminal 1: Start the Scheduler**
    ```bash
    source venv/bin/activate
    python scheduler_runner.py
    ```

2.  **Terminal 2: Start the Flask Development Server**
    ```bash
    source venv/bin/activate
    python app.py
    ```

### Production Deployment

In a production environment (like a Raspberry Pi), the application **must** be run as two separate, long-running processes. Using a tool like `screen` or `systemd` is highly recommended.

1.  **Process 1: The Scheduler**
    This process handles all time-critical background jobs.
    ```bash
    # Activate the environment
    source venv/bin/activate
    # Run the scheduler
    python3 scheduler_runner.py
    ```

2.  **Process 2: The Web Server (Gunicorn)**
    This process serves the API to the client application.
    ```bash
    # Activate the environment
    source venv/bin/activate
    # Run Gunicorn with 2-3 workers
    gunicorn -w 2 -b 0.0.0.0:5000 app:app
    ```
    *(Note: A worker count of 2 or 3 is a safe starting point for a Raspberry Pi. Adjust as needed.)*

## Testing

This project uses `pytest` for automated testing. The tests are located in the `tests/` directory and are organized by module.

### Test Coverage

The test suite currently includes over 40 tests, providing good coverage of the application's core functionality. The tests include:

-   **Unit Tests:**
    -   `tests/test_scraper.py`: Tests the web scraping logic in `scraper.py`, including parsing HTML and handling different booking scenarios.
    -   `tests/test_database.py`: Tests the database operations in `database.py` using an in-memory SQLite database to ensure test isolation.
-   **Integration Tests:**
    -   `tests/test_app.py`: Tests the Flask application's API endpoints, including authentication, protected routes, and the core booking and scheduling functionality.

### Running the Tests

To run the tests, navigate to the `gabs_api_server` directory and run the following command:

```bash
PYTHONPATH=. pytest
```

This command will discover and run all the tests in the `tests/` directory.

## API Endpoints Documentation

All endpoints (except `/api/login` and `/api/vapid-public-key`) require an `Authorization: Bearer <token>` header to be sent with the request.

---

### **Authentication**

#### `POST /api/login`

Authenticates a user with their gym credentials and returns a JWT access token for use with other endpoints.

-   **Request Body:**
    ```json
    {
      "username": "your_gym_email@example.com",
      "password": "your_gym_password"
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Content-Type: application/json" -d '{"username":"user@email.com", "password":"password123"}' http://127.0.0.1:5000/api/login
    ```
-   **Success Response (200 OK):**
    ```json
    {
      "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    }
    ```

#### `POST /api/logout`

Logs the user out by clearing their session from the server's cache.

-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/logout
    ```

---

### **Classes & Availability**

#### `GET /api/classes`

Gets a list of all available classes for the upcoming 7 days.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/classes
    ```

#### `GET /api/availability`

Gets the number of remaining spaces for a specific class on a given date.

-   **Query Parameters:**
    -   `class_name` (string, required): The name of the class.
    -   `date` (string, required): The date of the class in `YYYY-MM-DD` format.
-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" "http://127.0.0.1:5000/api/availability?class_name=Calorie%20Killer&date=2025-10-06"
    ```

---

### **Booking & Cancellation**

#### `POST /api/book`

Books a class or joins the waiting list if the class is full.

-   **Request Body:**
    ```json
    {
      "class_name": "Calorie Killer",
      "date": "2025-10-06",
      "time": "10:00"
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calorie Killer", "date":"2025-10-06"}' http://127.0.0.1:5000/api/book
    ```

#### `POST /api/cancel`

Cancels a booking for a class or removes the user from the waiting list.

-   **Request Body:** (Same as booking)
    ```json
    {
      "class_name": "Calorie Killer",
      "date": "2025-10-06",
      "time": "10:00"
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calorie Killer", "date":"2025-10-06", "time":"10:00"}' http://127.0.0.1:5000/api/cancel
    ```

#### `GET /api/bookings`

Gets a list of the authenticated user's current bookings and waiting list entries.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/bookings
    ```

---

### **Auto-Booking Functionality**

This API supports scheduling recurring automatic bookings for gym classes.

#### `POST /api/schedule_auto_book`

Schedules a new automatic booking.

-   **Request Body:**
    ```json
    {
      "class_name": "Calisthenics",
      "time": "10:00",
      "day_of_week": "Monday",
      "instructor": "George"
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calisthenics", "time":"10:00", "day_of_week":"Monday", "instructor":"George"}' http://127.0.0.1:5000/api/schedule_auto_book
    ```

#### `GET /api/auto_bookings`

Retrieves all scheduled automatic bookings for the current user.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/auto_bookings
    ```

#### `POST /api/cancel_auto_book`

Cancels a scheduled automatic booking.

-   **Request Body:**
    ```json
    {
      "booking_id": 123
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"booking_id":123}' http://127.0.0.1:5000/api/cancel_auto_book
    ```

---

### **Push Notifications**

The API supports push notifications for cancellation reminders.

#### `GET /api/vapid-public-key`

Retrieves the VAPID public key required for subscribing to push notifications.

-   **Example `curl` Request:**
    ```bash
    curl http://127.0.0.1:5000/api/vapid-public-key
    ```

#### `POST /api/subscribe-push`

Subscribes the current user to push notifications.

-   **Request Body:** (Web Push Subscription Object)
    ```json
    {
      "endpoint": "https://fcm.googleapis.com/fcm/send/...",
      "expirationTime": null,
      "keys": {
        "p256dh": "...",
        "auth": "..."
      }
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"endpoint":"...", "keys":{"p256dh":"...", "auth":"..."}}' http://127.0.0.1:5000/api/subscribe-push
    ```

---

### **Instructors**

#### `GET /api/instructors`

Gets a dictionary of all instructors, with each instructor's name as a key and a list of their scheduled classes as the value.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/instructors
    ```

#### `GET /api/classes-by-instructor`

Gets a list of all classes taught by a specific instructor.

-   **Query Parameters:**
    -   `name` (string, required): The name of the instructor.
-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" "http://127.0.0.1:5000/api/classes-by-instructor?name=George"
    ```

---

### **Admin Endpoints**

These endpoints are restricted to the administrator user defined in the `.env` file.

#### `GET /api/admin/logs`

Retrieves the last 100 lines of the server's log file.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <admin_token>" http://127.0.0.1:5000/api/admin/logs
    ```

#### `GET /api/admin/auto_bookings`

Retrieves a list of all scheduled automatic bookings across all users.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <admin_token>" http://127.0.0.1:5000/api/admin/auto_bookings
    ```

#### `GET /api/admin/push_subscriptions`

Retrieves a list of all active push subscriptions across all users.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <admin_token>" http://127.0.0.1:5000/api/admin/push/subscriptions
    ```

#### `GET /api/admin/status`

Retrieves basic status information about the server, including uptime and scraper cache size.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <admin_token>" http://127.0.0.1:5000/api/admin/status
    ```

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).