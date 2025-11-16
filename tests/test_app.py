import pytest
import json

def test_login_success(client, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {}
    mocker.patch('app.get_scraper_instance', return_value=mock_scraper)
    mocker.patch('database.save_session')
    
    response = client.post('/api/login', json={'username': 'test', 'password': 'pw'})
    
    assert response.status_code == 200
    assert 'access_token' in response.json

def test_login_failure(client, mocker):
    mocker.patch('app.get_scraper_instance', return_value=None)
    
    response = client.post('/api/login', json={'username': 'test', 'password': 'pw'})
    
    assert response.status_code == 401

@pytest.fixture
def auth_client(client, mocker):
    mock_scraper = mocker.Mock()
    mock_scraper.to_dict.return_value = {}
    mocker.patch('app.get_scraper_instance', return_value=mock_scraper)
    mocker.patch('database.save_session')
    
    response = client.post('/api/login', json={'username': 'test', 'password': 'pw'})
    token = response.json['access_token']
    
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    return client

def test_get_classes_with_auth(auth_client, mocker):
    get_scraper_instance_mock = mocker.patch('app.get_scraper_instance')
    mock_scraper = get_scraper_instance_mock.return_value
    mock_scraper.get_classes.return_value = [{'name': 'Test Class'}]
    
    response = auth_client.get('/api/classes')
    
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['name'] == 'Test Class'
