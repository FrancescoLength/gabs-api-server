import pytest
from flask import jsonify
from gabs_api_server.app import app, sync_live_bookings
from gabs_api_server import database

@pytest.fixture
def client(mocker):
    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret-key"
    # Patch limiter
    mocker.patch('gabs_api_server.app.limiter.limit', side_effect=lambda x: lambda f: f)
    with app.test_client() as client:
        yield client

@pytest.fixture
def auth_headers(client, mocker):
    username = "test_user"
    mocker.patch('gabs_api_server.database.load_session', return_value=("encrypted", {}))
    mocker.patch('gabs_api_server.crypto.decrypt', return_value="password")
    # Mock scraper creation for login
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {}
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    
    resp = client.post('/api/login', json={'username': username, 'password': 'password'})
    token = resp.json['access_token']
    return {'Authorization': f'Bearer {token}'}

def test_schedule_auto_book_success(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.add_auto_booking', return_value=123)
    
    data = {
        'class_name': 'Test Class',
        'time': '10:00',
        'day_of_week': 'Monday',
        'instructor': 'John'
    }
    response = client.post('/api/schedule_auto_book', headers=auth_headers, json=data)
    assert response.status_code == 201
    assert response.json['booking_id'] == 123

def test_schedule_auto_book_missing_params(client, auth_headers):
    response = client.post('/api/schedule_auto_book', headers=auth_headers, json={})
    assert response.status_code == 400
    assert 'required' in response.json['error']

def test_schedule_auto_book_error(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.add_auto_booking', side_effect=Exception("DB Error"))
    
    data = {
        'class_name': 'Test Class',
        'time': '10:00',
        'day_of_week': 'Monday',
        'instructor': 'John'
    }
    response = client.post('/api/schedule_auto_book', headers=auth_headers, json=data)
    assert response.status_code == 500
    assert 'internal server error' in response.json['error']

def test_get_auto_bookings_success(client, auth_headers, mocker):
    mock_bookings = [
        (1, 'test_user', 'Class', '10:00', 'pending', 'now', None, 0, 'Monday', 'John', None)
    ]
    mocker.patch('gabs_api_server.database.get_auto_bookings_for_user', return_value=mock_bookings)
    
    response = client.get('/api/auto_bookings', headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['class_name'] == 'Class'

def test_get_auto_bookings_error(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.get_auto_bookings_for_user', side_effect=Exception("DB Error"))
    
    response = client.get('/api/auto_bookings', headers=auth_headers)
    assert response.status_code == 500

def test_get_auto_bookings_null_values(client, auth_headers, mocker):
    # Simulate DB returning None for optional fields
    mock_bookings = [
        (1, 'test_user', 'Class', '10:00', 'pending', 'now', None, 0, 'Monday', None, None)
    ]
    mocker.patch('gabs_api_server.database.get_auto_bookings_for_user', return_value=mock_bookings)
    
    response = client.get('/api/auto_bookings', headers=auth_headers)
    assert response.status_code == 200
    data = response.json[0]
    assert data['instructor'] == ""
    assert data['last_booked_date'] == ""

def test_cancel_auto_book_success(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.cancel_auto_booking', return_value=True)
    
    response = client.post('/api/cancel_auto_book', headers=auth_headers, json={'booking_id': 1})
    assert response.status_code == 200
    assert 'cancelled successfully' in response.json['message']

def test_cancel_auto_book_not_found(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.cancel_auto_booking', return_value=False)
    
    response = client.post('/api/cancel_auto_book', headers=auth_headers, json={'booking_id': 999})
    assert response.status_code == 404
    assert 'Booking not found' in response.json['error']

def test_cancel_auto_book_missing_id(client, auth_headers):
    response = client.post('/api/cancel_auto_book', headers=auth_headers, json={})
    assert response.status_code == 400

def test_cancel_auto_book_error(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.cancel_auto_booking', side_effect=Exception("DB Error"))
    
    response = client.post('/api/cancel_auto_book', headers=auth_headers, json={'booking_id': 1})
    assert response.status_code == 500

def test_subscribe_push_success(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.save_push_subscription')
    
    response = client.post('/api/subscribe-push', headers=auth_headers, json={'endpoint': 'url'})
    assert response.status_code == 201
    assert 'successful' in response.json['message']

def test_subscribe_push_missing_info(client, auth_headers):
    response = client.post('/api/subscribe-push', headers=auth_headers, json={})
    assert response.status_code == 400

def test_subscribe_push_error(client, auth_headers, mocker):
    mocker.patch('gabs_api_server.database.save_push_subscription', side_effect=Exception("DB Error"))
    
    response = client.post('/api/subscribe-push', headers=auth_headers, json={'endpoint': 'url'})
    assert response.status_code == 500

def test_cancel_booking_success(client, auth_headers, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_cancel_booking.return_value = {'status': 'success'}
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mock_delete = mocker.patch('gabs_api_server.database.delete_live_booking')
    
    data = {'class_name': 'C', 'date': 'D', 'time': 'T'}
    response = client.post('/api/cancel', headers=auth_headers, json=data)
    assert response.status_code == 200
    mock_delete.assert_called()

def test_cancel_booking_failure(client, auth_headers, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.find_and_cancel_booking.return_value = {'status': 'error'}
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mock_delete = mocker.patch('gabs_api_server.database.delete_live_booking')
    
    data = {'class_name': 'C', 'date': 'D', 'time': 'T'}
    response = client.post('/api/cancel', headers=auth_headers, json=data)
    assert response.status_code == 200
    mock_delete.assert_not_called()

def test_cancel_booking_missing_params(client, auth_headers):
    response = client.post('/api/cancel', headers=auth_headers, json={})
    assert response.status_code == 400

def test_get_my_bookings_success(client, auth_headers, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.get_my_bookings.return_value = []
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=mock_scraper)
    mock_sync = mocker.patch('gabs_api_server.app.sync_live_bookings')
    mocker.patch('gabs_api_server.database.touch_session')
    
    response = client.get('/api/bookings', headers=auth_headers)
    assert response.status_code == 200
    mock_sync.assert_called()