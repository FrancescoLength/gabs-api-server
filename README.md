# GABS API Server: Automated Gym Booking & Management

This Flask-based API server empowers users to seamlessly interact with a famous Bristol gym's website, automating the process of viewing, booking, and managing gym class reservations. Designed for efficiency and convenience, it acts as a robust backend for custom client applications (such as a React frontend), ideal for deployment on low-power devices like a Raspberry Pi.

## Key Features

-   **Secure User Authentication:** Utilizes JWT for secure user login and session management.
-   **Comprehensive Class Overview:** Access a real-time list of available gym classes for the upcoming 7 days.
-   **Effortless Booking & Waitlisting:** Book classes directly or automatically join the waiting list if a class is full, ensuring you never miss a spot.
-   **Streamlined Cancellation:** Easily cancel existing bookings or remove yourself from waiting lists.
-   **Personalized Booking Management:** View all your upcoming class bookings and waiting list entries in one place.
-   **Real-time Availability Checks:** Instantly check the number of remaining spaces for any specific class.
-   **Instructor Insights:** Browse all instructors and their scheduled classes, with the ability to filter classes by a specific instructor.
-   **Automated Recurring Bookings:** Schedule and manage recurring auto-bookings for your favorite classes, ensuring you're always signed up.
-   **Intelligent Push Notifications:** Receive timely push notifications, including crucial cancellation reminders, to help you stay informed and avoid penalties.

## Setup and Installation

1.  **Prerequisites:**
    -   Python 3.8+

2.  **Installation:**
    ```bash
    # Navigate to the project directory
    cd gabs_api_server

    # (Recommended) Create and activate a virtual environment
    # On Windows:
    python -m venv venv
    venv\Scripts\activate
    
    # On Mac/Linux:
    python3 -m venv venv
    source venv/bin/activate

    # Install dependencies
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    -   Open the `config.py` file.
    -   **Important:** Change the default `JWT_SECRET_KEY` to a long, random, and secret string. This is critical for security.

4.  **Running the Server:**
    ```bash
    python app.py
    ```
    The API will be running at `http://127.0.0.1:5000`. To make it accessible on your network, it runs on `0.0.0.0`.

## API Endpoints Documentation

All endpoints (except `/api/login`) require an `Authorization: Bearer <token>` header to be sent with the request.

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

Gets a list of all available classes for the next 7 days.

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
