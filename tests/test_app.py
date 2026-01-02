# Tests for GABS Backend API app.py


def test_login_success(client, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {}
    mocker.patch('gabs_api_server.app.get_scraper_instance',
                 return_value=mock_scraper)
    mocker.patch('gabs_api_server.database.save_session')

    response = client.post(
        '/api/login', json={'username': 'test', 'password': 'pw'})

    assert response.status_code == 200
    assert 'access_token' in response.json


def test_login_failure(client, mocker):
    mocker.patch('gabs_api_server.app.get_scraper_instance', return_value=None)

    response = client.post(
        '/api/login', json={'username': 'test', 'password': 'pw'})

    assert response.status_code == 401


# Changed auth_client to client
def test_get_classes_with_auth(client, mocker):
    # Mock jwt_required/verify_jwt_in_request
    mocker.patch('gabs_api_server.app.verify_jwt_in_request',
                 return_value=True)
    mocker.patch('gabs_api_server.app.get_jwt_identity',
                 return_value='test_user')

    # Login to get a token (still needed for the mock scraper initialization)
    mock_scraper_login = mocker.Mock()
    mock_scraper_login.to_dict.return_value = {}
    mocker.patch('gabs_api_server.app.get_scraper_instance',
                 return_value=mock_scraper_login)
    mocker.patch('gabs_api_server.database.save_session')

    login_response = client.post(
        '/api/login', json={'username': 'test_user', 'password': 'pw'})
    token = login_response.json['access_token']

    # Mock get_scraper_instance for the actual API call
    get_scraper_instance_mock = mocker.patch(
        'gabs_api_server.app.get_scraper_instance')
    mock_scraper_for_api = get_scraper_instance_mock.return_value
    mock_scraper_for_api.get_classes.return_value = [{'name': 'Test Class'}]

    # Pass token explicitly
    response = client.get(
        '/api/classes', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['name'] == 'Test Class'
