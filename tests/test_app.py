import json

def test_static_classes(client):
    response = client.get('/api/static_classes')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert isinstance(data, dict)

def test_login_success(client, mocker):
    mocker.patch('app.Scraper', return_value=mocker.Mock())
    response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'access_token' in data

def test_login_failure(client, mocker):
    mocker.patch('app.Scraper', side_effect=Exception("Login failed"))
    response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    assert response.status_code == 401
    data = json.loads(response.data)
    assert 'error' in data

def test_get_classes_with_token(client, mocker):
    # Mock the Scraper class to avoid real login attempts
    scraper_mock = mocker.Mock()
    scraper_mock.get_classes.return_value = []
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})

    # First, login to get a token
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    # Now, access the protected endpoint with the token
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.get('/api/classes', headers=headers)
    assert response.status_code == 200

def test_get_classes_no_token(client):
    response = client.get('/api/classes')
    assert response.status_code == 401

def test_schedule_auto_book_success(client, mocker):
    mocker.patch('app.database.add_auto_booking', return_value=1)
    scraper_mock = mocker.Mock()
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/schedule_auto_book', headers=headers, json={
        'class_name': 'Test Class',
        'time': '10:00',
        'day_of_week': 'Monday',
        'instructor': 'Test Instructor'
    })

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data['message'] == 'Recurring auto-booking scheduled successfully'

def test_schedule_auto_book_missing_params(client, mocker):
    mocker.patch('app.Scraper', return_value=mocker.Mock())
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/schedule_auto_book', headers=headers, json={
        'class_name': 'Test Class'
    })

    assert response.status_code == 400
    data = json.loads(response.data)
    assert 'error' in data

def test_get_auto_bookings_success(client, mocker):
    mocker.patch('app.database.get_auto_bookings_for_user', return_value=[])
    scraper_mock = mocker.Mock()
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.get('/api/auto_bookings', headers=headers)

    assert response.status_code == 200
    data = json.loads(response.data)
    assert isinstance(data, list)

def test_cancel_auto_book_success(client, mocker):
    mocker.patch('app.database.cancel_auto_booking', return_value=True)
    scraper_mock = mocker.Mock()
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/cancel_auto_book', headers=headers, json={
        'booking_id': 1
    })

    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'cancelled successfully' in data['message']

def test_cancel_auto_book_missing_params(client, mocker):
    mocker.patch('app.Scraper', return_value=mocker.Mock())
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/cancel_auto_book', headers=headers, json={})

    assert response.status_code == 400
    data = json.loads(response.data)
    assert 'error' in data

def test_get_availability_success(client, mocker):
    scraper_mock = mocker.Mock()
    scraper_mock.get_class_availability.return_value = {"remaining_spaces": 5}
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.get('/api/availability?class_name=Test%20Class&date=2025-10-26', headers=headers)

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['remaining_spaces'] == 5

def test_get_instructors_success(client, mocker):
    scraper_mock = mocker.Mock()
    scraper_mock.get_classes.return_value = [
        {'instructor': 'Test Instructor 1', 'name': 'Test Class 1', 'date': '26/10/2025', 'time': '10:00'},
        {'instructor': 'Test Instructor 2', 'name': 'Test Class 2', 'date': '26/10/2025', 'time': '12:00'}
    ]
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.get('/api/instructors', headers=headers)

    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'Test Instructor 1' in data
    assert 'Test Instructor 2' in data

def test_get_classes_by_instructor_success(client, mocker):
    scraper_mock = mocker.Mock()
    scraper_mock.get_classes.return_value = [
        {'instructor': 'Test Instructor 1', 'name': 'Test Class 1'}
    ]
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.get('/api/classes-by-instructor?name=Test%20Instructor%201', headers=headers)

    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) == 1
    assert data[0]['name'] == 'Test Class 1'

def test_logout_success(client, mocker):
    scraper_mock = mocker.Mock()
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/logout', headers=headers)

    assert response.status_code == 200

def test_get_vapid_public_key_success(client, mocker):
    mocker.patch('app.config.VAPID_PUBLIC_KEY', 'test_key')
    response = client.get('/api/vapid-public-key')
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'test_key'

def test_subscribe_push_success(client, mocker):
    mocker.patch('app.database.save_push_subscription')
    scraper_mock = mocker.Mock()
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/subscribe-push', headers=headers, json={
        'endpoint': 'test_endpoint',
        'keys': {'p256dh': 'test', 'auth': 'test'}
    })

    assert response.status_code == 201

def test_test_push_notification_success(client, mocker):
    mocker.patch('app.database.get_all_push_subscriptions', return_value=[])
    mocker.patch('app.webpush')
    scraper_mock = mocker.Mock()
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'admin@example.com',
        'password': 'admin_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/test-push-notification', headers=headers)

    assert response.status_code == 200

def test_book_class_success(client, mocker):
    scraper_mock = mocker.Mock()
    scraper_mock.find_and_book_class.return_value = {"status": "success"}
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/book', headers=headers, json={
        'class_name': 'Test Class',
        'date': '2025-10-26',
        'time': '10:00'
    })

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'success'

def test_cancel_booking_success(client, mocker):
    scraper_mock = mocker.Mock()
    scraper_mock.find_and_cancel_booking.return_value = {"status": "success"}
    mocker.patch('app.scraper_cache', {'test_user': scraper_mock})
    mocker.patch('app.Scraper', return_value=scraper_mock)
    login_response = client.post('/api/login', json={
        'username': 'test_user',
        'password': 'test_pass'
    })
    access_token = json.loads(login_response.data)['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    response = client.post('/api/cancel', headers=headers, json={
        'class_name': 'Test Class',
        'date': '2025-10-26',
        'time': '10:00'
    })

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'success'
