# GABS API Server: Automated Gym Booking & Management

This Flask-based API server empowers users to seamlessly interact with a famous Bristol gym's website, automating the process of viewing, booking, and managing gym class reservations. Designed for efficiency and convenience, it acts as a robust backend for custom client applications (such as a React frontend), ideal for deployment on low-power devices like a Raspberry Pi.

## Key Features

-   **Secure User Authentication:** Utilizes JWT for secure user login and session management.
-   **Comprehensive Class Overview:** Access a real-time list of available gym classes for the upcoming 7 days.
-   **Effortless Booking & Waitlisting:** Book classes directly or automatically join the waiting list if a class is full.
-   **Streamlined Cancellation:** Easily cancel existing bookings.
-   **Personalized Booking Management:** View all your upcoming class bookings and waiting list entries.
-   **Automated Recurring Bookings:** Schedule and manage recurring auto-bookings for your favorite classes. The scheduler runs in a dedicated process to ensure time-critical bookings are handled with high precision and concurrency.
-   **Intelligent Push Notifications:** Receive timely push notifications, including crucial cancellation reminders.
-   **Admin Panel:** Dedicated endpoints for administrators to monitor logs, auto-bookings, push subscriptions, and server status.
-   **Automatic API Documentation:** Interactive Swagger UI documentation available at `/apidocs`.

## Credential Management and Security

The GABS API Server prioritizes the security of user credentials. The system employs robust measures to handle sensitive information:

*   **Encrypted Storage:** User passwords are never stored in plaintext. Instead, they are securely encrypted using a strong symmetric encryption scheme (Fernet from the `cryptography` library) and persisted in the SQLite database.
*   **Environment Variables:** All sensitive keys (`ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `VAPID_PRIVATE_KEY`) are strictly loaded from environment variables. **There are no fallback file-based keys.** This practice prevents sensitive data from being exposed in version control.
*   **Secure Session Management:** Upon successful authentication, encrypted credentials are utilized to establish and maintain a secure session with the gym's website. Session-specific data (cookies, CSRF tokens) is securely stored in an encrypted format within the SQLite database. An in-memory cache is explicitly avoided to minimize RAM usage on resource-constrained devices. A proactive background job periodically refreshes these sessions to ensure they remain active, maximizing reliability for time-critical bookings.
*   **Strict Access Control:** Encrypted user passwords can only be accessed and decrypted by the automated booking system when strictly necessary to perform booking or scraping operations on behalf of the user.
*   **Rate Limiting:** The login endpoint is protected with rate limiting, mitigating the risk of brute-force password guessing attacks.

## Architecture Overview

The application is designed with a decoupled architecture to ensure stability and performance, especially for time-sensitive tasks.

-   **`app.py`**: The core Flask web application. Its sole responsibility is to handle incoming API requests from the client. It does not run any background jobs itself.
-   **`scheduler_runner.py`**: A standalone, dedicated process that runs the APScheduler for all background tasks. It uses a thread pool to execute jobs concurrently, which is critical for handling multiple auto-booking requests for the same class without delays.
-   **`logging_config.py`**: A centralized module that provides a consistent logging format for both the web server and the scheduler processes.
-   **`scraper.py`**: A robust web scraping client that handles all interactions with the gym's website, including advanced headers to mimic a real browser and prevent blocking.
-   **`static_timetable.json`**: Stores a static version of the gym's timetable, used as a fallback or for quick lookups.
-   **Proactive Session Refresh:** A dedicated scheduler job (`refresh_sessions`) runs periodically (every 2 hours) to ensure all active user sessions with the gym's website remain valid. This minimizes the risk of session expiration during critical booking windows.
-   **Asynchronous & Non-Blocking Booking Logic**: The core auto-booking process has been heavily optimized for speed and reliability. The previous "pre-warming" step has been removed in favor of the proactive session refresh. Furthermore, I/O-intensive operations like writing debug HTML files upon failure are now handled by a dedicated, non-blocking background thread. This ensures that the main booking process is never delayed by slow disk operations, maximizing the chances of successfully booking a spot in a competitive, time-sensitive environment.

## Setup and Installation

1.  **Prerequisites:**
    -   Python 3.12+
    -   Git
    -   Docker & Docker Compose (Optional but recommended)

2.  **Clone the Repository:**
    ```bash
    git clone https://github.com/FrancescoLength/gabs-api-server.git
    cd gabs_api_server
    ```

3.  **Configuration (.env file):**
    This project strictly uses environment variables.
    -   Create a file named `.env` in the root of the `gabs_api_server` directory.
    -   Copy content from `.env.example` as a template.
    -   **CRITICAL:** You MUST provide `ENCRYPTION_KEY` and `JWT_SECRET_KEY`. You can generate secure keys using Python:
        ```python
        from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
        import secrets; print(secrets.token_urlsafe(32))
        ```

4.  **Generate VAPID Keys (if needed):**
    If you need to generate new VAPID keys for push notifications, run the provided script:
    ```bash
    python generate_vapid_keys_manual.py
    ```
    Update your `.env` file with the generated `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`.

## Running the Server

### Option A: Docker Deployment (Recommended)

1.  **Build and Run:**
    ```bash
    docker-compose up --build -d
    ```
    The API will be available at `http://localhost:5000`.

### Option B: Manual Deployment (Local/Systemd)

1.  **Install Dependencies:**
    ```bash
    # Create and activate a virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install dependencies
    pip install -r requirements.txt
    ```

2.  **Running Locally:**
    *   **Terminal 1 (Scheduler):** `python scheduler_runner.py`
    *   **Terminal 2 (Web Server):** `python app.py` (or via Gunicorn)

3.  **Production Deployment (systemd):**

For a robust production deployment on a Linux system (like a Raspberry Pi), it is highly recommended to manage the Gunicorn web server, the scheduler, and the Ngrok tunnel as `systemd` services.

**1. Create the Service Files:**

You will need to create three service files in `/etc/systemd/system/`.

**`gabs-api.service`** (for Gunicorn)
```ini
[Unit]
Description=GABS API Gunicorn Service
After=network.target

[Service]
User=gabs-admin
Group=gabs-admin
WorkingDirectory=/home/gabs-admin/gabs-api-server
ExecStart=/home/gabs-admin/gabs-api-server/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

**`gabs-scheduler.service`** (for the Scheduler)
```ini
[Unit]
Description=GABS Scheduler Service
After=network.target

[Service]
User=gabs-admin
Group=gabs-admin
WorkingDirectory=/home/gabs-admin/gabs-api-server
ExecStart=/home/gabs-admin/gabs-api-server/venv/bin/python3 scheduler_runner.py
Restart=always

[Install]
WantedBy=multi-user.target
```

**`gabs-ngrok.service`** (for Ngrok)
```ini
[Unit]
Description=Ngrok Tunnel for GABS API
After=network.target

[Service]
User=gabs-admin
Group=gabs-admin
ExecStart=/usr/local/bin/ngrok start --all --config /home/gabs-admin/.config/ngrok/ngrok.yml
Restart=always

[Install]
WantedBy=multi-user.target
```
*(Note: Ensure the `User`, `Group`, and paths in `WorkingDirectory` and `ExecStart` match your specific setup.)*

**2. Enable and Start the Services:**

```bash
# Reload the systemd daemon
sudo systemctl daemon-reload

# Enable and start services
sudo systemctl enable --now gabs-api.service gabs-scheduler.service gabs-ngrok.service
```

## Testing

This project uses `pytest` for automated testing. The tests are located in the `tests/` directory and are organized by module.

### Test Coverage

The test suite currently includes over 40 tests, providing good coverage of the application's core functionality. The tests include:

-   **Unit Tests:**
    -   `tests/test_scraper.py`: Tests the web scraping logic in `scraper.py`, including parsing HTML and handling different booking scenarios.
    -   `tests/test_database.py`: Tests the database operations in `database.py` using an in-memory SQLite database to ensure test isolation.
-   **Integration Tests:**
    -   `tests/test_app.py`: Tests the Flask application's API endpoints, including authentication, protected routes, and the core booking and scheduling functionality.

### Test Coverage Report

Overall test coverage: **94%**

| File                                                 | Statements | Missing | Coverage |
| :--------------------------------------------------- | :--------- | :------ | :------- |
| `gabs_api_server/app.py`                             | 438        | 12      | 97%      |
| `gabs_api_server/config.py`                          | 20         | 7       | 65%      |
| `gabs_api_server/crypto.py`                          | 16         | 2       | 88%      |
| `gabs_api_server/database.py`                        | 257        | 36      | 86%      |
| `gabs_api_server/generate_encryption_key.py`         | 8          | 8       | 0%       |
| `gabs_api_server/generate_vapid_keys_manual.py`      | 14         | 14      | 0%       |
| `gabs_api_server/logging_config.py`                  | 25         | 0       | 100%     |
| `gabs_api_server/scheduler_runner.py`                | 49         | 7       | 86%      |
| `gabs_api_server/scraper.py`                         | 347        | 50      | 86%      |
| `gabs_api_server/services/auto_booking_service.py`   | 115        | 22      | 81%      |
| `gabs_api_server/tests/conftest.py`                  | 27         | 0       | 100%     |
| `gabs_api_server/tests/test_app.py`                  | 31         | 0       | 100%     |
| `gabs_api_server/tests/test_app_endpoints.py`        | 116        | 0       | 100%     |
| `gabs_api_server/tests/test_app_integration.py`      | 195        | 0       | 100%     |
| `gabs_api_server/tests/test_app_more_units.py`       | 101        | 0       | 100%     |
| `gabs_api_server/tests/test_app_units.py`            | 122        | 1       | 99%      |
| `gabs_api_server/tests/test_auto_booking_service.py` | 140        | 0       | 100%     |
| `gabs_api_server/tests/test_crypto.py`               | 29         | 0       | 100%     |
| `gabs_api_server/tests/test_database.py`             | 192        | 3       | 98%      |
| `gabs_api_server/tests/test_scheduler_jobs.py`       | 178        | 0       | 100%     |
| `gabs_api_server/tests/test_scheduler_runner.py`     | 29         | 0       | 100%     |
| `gabs_api_server/tests/test_scraper.py`              | 253        | 0       | 100%     |
| **TOTAL**                                            | **2702**   | **162** | **94%**  |

### Running the Tests
```bash
PYTHONPATH=. pytest --cov=gabs_api_server gabs_api_server/tests/
```

### CI/CD Pipeline
A GitHub Actions workflow (`.github/workflows/test.yml`) ensures code quality on every contribution.

-   **Triggers**: Runs on `push` and `pull_request` to `main` or `master` branches.
-   **Linting**: Uses `flake8` to enforce style guidelines and catch syntax errors.
-   **Testing**: Executes the full `pytest` suite with coverage reporting.

### Deployment (Raspberry Pi)
Unlike the frontend, the backend does not deployed automatically via CI (due to the on-premise nature of the Raspberry Pi).
**Recommended Deployment Workflow:**
1.  **CI Validation**: Ensure the GitHub Actions workflow passes.
2.  **Pull Updates**: On the Raspberry Pi, run `git pull`.
3.  **Restart Services**: `docker-compose up -d --build` or `sudo systemctl restart gabs-api`.

## API Endpoints Documentation

Interactive API documentation is available via Swagger UI at `/apidocs`.

All endpoints (except `/api/login` and `/api/vapid-public-key`) require an `Authorization: Bearer <token>` header.

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
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calorie Killer", "date":"2025-10-06", "time":"10:00"}' http://127.0.0.1:5000/api/book
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

This API supports scheduling recurring automatic bookings for gym classes. All auto-booking configurations are stored in the `auto_bookings.db` SQLite database.

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
    curl -H "Authorization: Bearer <admin_token>" http://127.0.0.1:5000/api/admin/push_subscriptions
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