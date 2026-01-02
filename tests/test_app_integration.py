from datetime import datetime
from gabs_api_server.app import sync_live_bookings
from gabs_api_server import database
import pytest # Import pytest for assert_called_once
from requests.exceptions import RequestException
from gabs_api_server.scraper import SessionExpiredError

def test_sync_live_bookings_add_and_delete(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    database.add_live_booking(username, "Old Class", "2025-01-01", "10:00")
    
    scraped_bookings = [
        {"name": "New Class", "date": "Monday 1st January", "time": "12:00", "status": "Booked"}
    ]
    
    # 2. Execute
    sync_live_bookings(username, scraped_bookings)
    
    # 3. Assert
    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 1
    assert live_bookings[0][2] == "New Class"

def test_sync_live_bookings_case_insensitivity(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    database.add_live_booking(username, "BoxFit", "2025-01-01", "10:00")
    
    scraped_bookings = [
        {"name": "Boxfit", "date": "Monday 1st January", "time": "10:00", "status": "Booked"}
    ]
    
    # 2. Execute
    sync_live_bookings(username, scraped_bookings)
    
    # 3. Assert
    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 1
    # The name should be updated to the one from the scraper
    assert live_bookings[0][2] == "Boxfit"

def test_sync_live_bookings_invalid_date_format(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    # Scraped booking with an invalid date format
    scraped_bookings = [
        {"name": "Bad Date Class", "date": "Not a Date", "time": "10:00", "status": "Booked"}
    ]
    
    # Mock logging.error to check if it's called
    mock_logger_error = mocker.patch('gabs_api_server.app.logging.error')

    # 2. Execute
    sync_live_bookings(username, scraped_bookings)
    
    # 3. Assert
    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 0 # No booking should be added
    mock_logger_error.assert_called_once()
    assert "Error parsing date" in mock_logger_error.call_args[0][0]

def test_sync_live_bookings_full_booking_not_found(memory_db, mocker):
    # 1. Setup
    username = "test_user"
    current_year = datetime.now().year
    class_date = f"{current_year}-01-01"
    database.add_live_booking(username, "Existing Class", class_date, "10:00")
    
    # Scraped booking that is valid, but full_booking won't be found
    # (e.g., if there's a mismatch between scraped_bookings_set key and original scraped_bookings)
    scraped_bookings = [
        {"name": "Existing Class", "date": "Monday 1st January", "time": "10:00", "status": "Booked", "instructor": "John Doe"}
    ]
    
    # Mock database.add_live_booking to check if it's called
    mock_add_live_booking = mocker.patch('gabs_api_server.database.add_live_booking')

    # Simulate a scenario where `full_booking` would be None in the next(b for b in scraped_bookings ...) call
    # This is tricky as the `next` call directly operates on the `scraped_bookings` list.
    # To hit this branch, we need `class_name_lower` and `class_time` to not match any in `scraped_bookings`.
    # Let's ensure scraped_bookings_set is created but the original scraped_bookings list is empty or doesn't match
    mocker.patch('gabs_api_server.app.next', side_effect=[None]) # Make next return None for the first call

    # 2. Execute
    sync_live_bookings(username, scraped_bookings)
    
    # 3. Assert
    # The booking should still be in the DB from the original add_live_booking call, but no new ones should be added
    live_bookings = database.get_live_bookings_for_user(username)
    assert len(live_bookings) == 1
    assert live_bookings[0][2] == "Existing Class"
    mock_add_live_booking.assert_not_called()


def test_admin_endpoints_access(client, mocker):
    # Mock scraper and login
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mocker.Mock())
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', 'admin@example.com')
    
    # Login as admin
    admin_login_resp = client.post('/api/login', json={'username': 'admin@example.com', 'password': 'password'})
    admin_token = admin_login_resp.json['access_token']
    
    # Login as normal user
    user_login_resp = client.post('/api/login', json={'username': 'test@example.com', 'password': 'password'})
    user_token = user_login_resp.json['access_token']
    
    # Admin headers
    admin_headers = {'Authorization': f'Bearer {admin_token}'}
    # User headers
    user_headers = {'Authorization': f'Bearer {user_token}'}

    # List of (endpoint, admin_mock_function_path, admin_mock_return_value)
    # Admin logs is handled by default test in app.py
    endpoints_to_test = [
        ('/api/admin/auto_bookings', 'gabs_api_server.database.get_all_auto_bookings', []),
        ('/api/admin/live_bookings', 'gabs_api_server.database.get_all_live_bookings', []),
        ('/api/admin/push_subscriptions', 'gabs_api_server.database.get_all_push_subscriptions', []),
        ('/api/admin/sessions', 'gabs_api_server.database.get_all_sessions', []),
        ('/api/admin/status', None, {"status": "ok", "uptime": "123"}) # status mocks requests.get
    ]

    for endpoint, mock_path, mock_return_value in endpoints_to_test:
        # Mock the underlying database call or external dependency for admin access
        if mock_path:
            mocker.patch(mock_path, return_value=mock_return_value)
        if endpoint == '/api/admin/status':
            mocker.patch('gabs_api_server.app.requests.get', return_value=mocker.Mock(status_code=200, json=lambda: {'tunnels': []}))


        # Test admin access
        admin_resp = client.get(endpoint, headers=admin_headers)
        assert admin_resp.status_code == 200, f"Admin failed for {endpoint}"
        
        # Test user access
        user_resp = client.get(endpoint, headers=user_headers)
        assert user_resp.status_code == 403, f"User granted access for {endpoint}"

def test_admin_endpoints_get_status_ngrok_failure(client, mocker):
    # Mock scraper and login for admin
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mocker.Mock())
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', 'admin@example.com')
    admin_login_resp = client.post('/api/login', json={'username': 'admin@example.com', 'password': 'password'})
    admin_token = admin_login_resp.json['access_token']
    admin_headers = {'Authorization': f'Bearer {admin_token}'}

    # Mock requests.get to raise a RequestException (Ngrok failure)
    mocker.patch('gabs_api_server.app.requests.get', side_effect=RequestException("Ngrok connection failed"))
    
    # Mock logging.error to check if it's called
    mock_logger_error = mocker.patch('gabs_api_server.app.logging.error')

    # Test admin access to /api/admin/status
    admin_resp = client.get('/api/admin/status', headers=admin_headers)
    assert admin_resp.status_code == 200 # Should still return 200 OK, but with no ssh_tunnel_command
    assert admin_resp.json['status'] == 'ok'
    assert admin_resp.json['ssh_tunnel_command'] is None
    mock_logger_error.assert_called_once()
    assert "Could not fetch ngrok tunnels" in mock_logger_error.call_args[0][0]

def test_api_login_invalid_credentials(client, mocker):
    # Mock get_scraper_instance to simulate login failure
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=None)
    
    # Attempt login with invalid credentials
    response = client.post('/api/login', json={'username': 'invalid@example.com', 'password': 'wrong_password'})
    assert response.status_code == 401
    assert response.json['error'] == 'Invalid credentials or login failed'

def test_api_login_missing_credentials(client):
    # Attempt login with missing username
    response = client.post('/api/login', json={'password': 'password'})
    assert response.status_code == 400
    assert response.json['error'] == 'Username and password required'

    # Attempt login with missing password
    response = client.post('/api/login', json={'username': 'user@example.com'})
    assert response.status_code == 400
    assert response.json['error'] == 'Username and password required'

def test_api_logout_success(client, mocker):
    # Mock get_scraper_instance for login success
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {}
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mocker.patch('gabs_api_server.database.save_session')
    
    # Login to get a valid token
    login_resp = client.post('/api/login', json={'username': 'user@example.com', 'password': 'password'})
    token = login_resp.json['access_token']
    
    # Mock database.delete_session
    mock_delete_session = mocker.patch('gabs_api_server.database.delete_session', return_value=True)

    # Attempt logout with the valid token
    logout_resp = client.post('/api/logout', headers={'Authorization': f'Bearer {token}'})
    assert logout_resp.status_code == 200
    assert logout_resp.json['message'] == 'Successfully logged out'
    mock_delete_session.assert_called_once_with('user@example.com')

def test_api_logout_unauthorized(client):
    # Attempt logout without a token
    response = client.post('/api/logout')
    assert response.status_code == 401
    assert response.json['msg'] == 'Missing Authorization Header'

def test_api_book_success(client, mocker):
    username = "test_user"
    class_name = "Test Class"
    target_date = "2025-01-01"
    target_time = "10:00"

    # Mock get_scraper_instance and its book_class method
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_book_class.return_value = {"status": "success", "message": "Booking successful"}
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    
    # Mock database.add_live_booking
    mock_add_live_booking = mocker.patch('gabs_api_server.database.add_live_booking')

    # Login to get a token
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")
    login_resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = login_resp.json['access_token']
    
    # NEW: Mock get_jwt_identity for this test
    mocker.patch('gabs_api_server.app.get_jwt_identity', return_value=username)
    
    # Make the API call
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': class_name,
        'date': target_date,
        'time': target_time
    })

    # Assertions
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    mock_scraper.find_and_book_class.assert_called_once_with(
        target_date_str=target_date, class_name=class_name, target_time=target_time
    )

def test_api_book_missing_parameters(client, mocker):
    username = "test_user"

    # Mock get_scraper_instance for login success
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {} # Mock to_dict if it's called during save_session
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)

    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")
    login_resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = login_resp.json['access_token']
    mocker.patch('gabs_api_server.app.get_jwt_identity', return_value=username)

    # Test case 1: Missing class_name
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'date': "2025-01-01",
        'time': "10:00"
    })
    assert response.status_code == 400
    assert response.json['error'] == 'class_name, date, and time are required.'

    # Test case 2: Missing date
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': "Test Class",
        'time': "10:00"
    })
    assert response.status_code == 400
    assert response.json['error'] == 'class_name, date, and time are required.'

    # Test case 3: Missing time
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': "Test Class",
        'date': "2025-01-01"
    })
    assert response.status_code == 400
    assert response.json['error'] == 'class_name, date, and time are required.'

def test_api_book_session_not_found(client, mocker):
    username = "test_user"
    class_name = "Test Class"
    target_date = "2025-01-01"
    target_time = "10:00"

    # Mock get_scraper_instance for the LOGIN to succeed
    mock_login_scraper = mocker.Mock()
    mock_login_scraper.to_dict.return_value = {} # Needed for successful session saving during login
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_login_scraper) # This patch is for the login call

    # Login to get a token
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")
    login_resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = login_resp.json['access_token']
    
    # Mock get_jwt_identity for this test
    mocker.patch('gabs_api_server.app.get_jwt_identity', return_value=username)

    # NOW, mock get_scraper_instance to return None *specifically for the /api/book call*
    # This overwrites the previous patch for the duration of the /api/book call
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=None) 

    # Make the API call
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': class_name,
        'date': target_date,
        'time': target_time
    })

    # Assertions
    assert response.status_code == 401
    assert response.json['error'] == 'Session not found. Please log in again.'

def test_api_book_session_expired(client, mocker):
    username = "test_user"
    class_name = "Test Class"
    target_date = "2025-01-01"
    target_time = "10:00"

    # Mock get_scraper_instance to return a scraper mock that raises SessionExpiredError
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_book_class.side_effect = SessionExpiredError("Session is stale")
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)

    # Login to get a token
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")
    login_resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = login_resp.json['access_token']
    
    # Mock get_jwt_identity for this test
    mocker.patch('gabs_api_server.app.get_jwt_identity', return_value=username)
    # Mock handle_session_expiration to check if it's called
    mock_handle_session_expiration = mocker.patch('gabs_api_server.app.handle_session_expiration')

    # Make the API call
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': class_name,
        'date': target_date,
        'time': target_time
    })

    # Assertions
    assert response.status_code == 401
    assert response.json['error'] == 'Your session has expired. Please log in again.'
    mock_handle_session_expiration.assert_called_once_with(username)
    mock_scraper.find_and_book_class.assert_called_once_with(
        target_date_str=target_date, class_name=class_name, target_time=target_time
    )

def test_api_book_generic_exception(client, mocker):
    username = "test_user"
    class_name = "Test Class"
    target_date = "2025-01-01"
    target_time = "10:00"

    # Mock get_scraper_instance to return a scraper mock that raises a generic Exception
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_book_class.side_effect = Exception("Something unexpected happened")
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)

    # Login to get a token
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted_pw", {"cookies": {}}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="plain_password")
    login_resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = login_resp.json['access_token']
    
    # Mock get_jwt_identity for this test
    mocker.patch('gabs_api_server.app.get_jwt_identity', return_value=username)
    # Mock logging.error to check if it's called
    mock_logger_error = mocker.patch('gabs_api_server.app.logging.error')

    # Make the API call
    response = client.post('/api/book', headers={'Authorization': f'Bearer {token}'}, json={
        'class_name': class_name,
        'date': target_date,
        'time': target_time
    })

    # Assertions
    assert response.status_code == 500
    assert response.json['error'] == 'An internal server error occurred.'
    mock_logger_error.assert_called_once()
    assert "Unhandled error in scraper endpoint" in mock_logger_error.call_args[0][0]
    mock_scraper.find_and_book_class.assert_called_once_with(
        target_date_str=target_date, class_name=class_name, target_time=target_time
    )