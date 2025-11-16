import pytest
from app import sync_live_bookings
import database

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

def test_admin_endpoints_access(client, mocker):
    # Mock scraper and login
    mocker.patch('app.get_scraper_instance', return_value=mocker.Mock())
    mocker.patch('app.config.ADMIN_EMAIL', 'admin@example.com')
    
    # Login as admin
    admin_login_resp = client.post('/api/login', json={'username': 'admin@example.com', 'password': 'password'})
    admin_token = admin_login_resp.json['access_token']
    
    # Login as normal user
    user_login_resp = client.post('/api/login', json={'username': 'test@example.com', 'password': 'password'})
    user_token = user_login_resp.json['access_token']
    
    # Test admin access
    admin_headers = {'Authorization': f'Bearer {admin_token}'}
    mocker.patch('app.open', mocker.mock_open(read_data=''))
    admin_resp = client.get('/api/admin/logs', headers=admin_headers)
    assert admin_resp.status_code == 200
    
    # Test user access
    user_headers = {'Authorization': f'Bearer {user_token}'}
    user_resp = client.get('/api/admin/logs', headers=user_headers)
    assert user_resp.status_code == 403
