# Gym Booking API

A Flask-based API to interact with the Workout Bristol website, allowing users to view, book, and manage gym class bookings.

This API is designed to be run on a low-power device like a Raspberry Pi and serves as a backend for a custom client application (e.g., a React app).

## Features

-   User authentication via JWT.
-   View available classes for the next 7 days.
-   Book a class or join the waiting list if it's full.
-   Cancel a booking or a waiting list spot.
-   View all personal upcoming bookings and waiting list entries.
-   Check the number of available spaces for a specific class.
-   List all instructors and their scheduled classes.
-   Filter classes by a specific instructor.

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
      "date": "2025-10-06"
    }
    ```
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calorie Killer", "date":"2025-10-06"}' http://127.0.0.1:5000/api/book
    ```

#### `POST /api/cancel`

Cancels a booking for a class or removes the user from the waiting list.

-   **Request Body:** (Same as booking)
-   **Example `curl` Request:**
    ```bash
    curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"class_name":"Calorie Killer", "date":"2025-10-06"}' http://127.0.0.1:5000/api/cancel
    ```

#### `GET /api/bookings`

Gets a list of the authenticated user's current bookings and waiting list entries.

-   **Example `curl` Request:**
    ```bash
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:5000/api/bookings
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
    curl -H "Authorization: Bearer <token>" "http://127.0.0.1:5000/api/classes-by-instructor?name=Zoe"
    ```
