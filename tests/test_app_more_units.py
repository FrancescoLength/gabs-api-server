import pytest
import requests
from unittest.mock import mock_open
from flask_jwt_extended import create_access_token
from gabs_api_server.app import debug_file_writer, app


def test_debug_file_writer_success(mocker):
    # This test is a placeholder or can be removed if covered by logic test
    pass


def test_debug_file_writer_logic(mocker):
    # Test the logic of the writer function directly by mocking the queue
    mock_queue = mocker.Mock()
    # Return one item, then (None, None) to break the loop
    mock_queue.get.side_effect = [
        ('/path/to/file.txt', 'content'), (None, None)]

    mock_open_func = mocker.mock_open()
    mocker.patch('builtins.open', mock_open_func)
    mock_logging_info = mocker.patch('gabs_api_server.app.logging.info')
    mock_queue.task_done = mocker.Mock()

    mocker.patch('gabs_api_server.app.debug_writer_queue', mock_queue)

    # Run the function
    debug_file_writer()

    mock_open_func.assert_called_with(
        '/path/to/file.txt', 'w', encoding='utf-8')
    mock_open_func().write.assert_called_with('content')
    mock_logging_info.assert_called()


def test_debug_file_writer_error(mocker):
    mock_queue = mocker.Mock()
    # 1. Valid item (will cause IOError on open)
    # 2. Termination item (None, None) to break the loop
    mock_queue.get.side_effect = [
        ('/path/to/file.txt', 'content'), (None, None)]

    # Mock open to raise IOError
    mocker.patch('builtins.open', side_effect=IOError("Write failed"))
    mock_logging_error = mocker.patch('gabs_api_server.app.logging.error')
    mocker.patch('gabs_api_server.app.debug_writer_queue', mock_queue)

    debug_file_writer()

    mock_logging_error.assert_called()
    assert "Error in debug file writer thread" in mock_logging_error.call_args[0][0]


@pytest.fixture
def test_client():
    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret-key"
    app.config["ADMIN_EMAIL"] = "admin@example.com"
    # Ensure JWT is configured correctly for the app
    app.config["JWT_TOKEN_LOCATION"] = ["headers"]
    app.config["JWT_HEADER_NAME"] = "Authorization"
    app.config["JWT_HEADER_TYPE"] = "Bearer"

    with app.test_client() as client:
        with app.app_context():
            yield client


def test_get_logs_success(test_client, mocker):
    # Use real token logic
    admin_token = create_access_token(identity="admin@example.com")

    # Mock config to ensure admin check passes
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', "admin@example.com")

    log_content = "2025-01-01 10:00:00 - INFO - Test log message\nSome raw text line\n"
    mocker.patch('builtins.open', mock_open(read_data=log_content))
    mocker.patch('gabs_api_server.app.os.path.exists', return_value=True)

    response = test_client.get(
        '/api/admin/logs', headers={'Authorization': f'Bearer {admin_token}'})
    assert response.status_code == 200
    logs = response.json['logs']
    assert len(logs) > 0

    found_msg = False
    for log in logs:
        if log['message'] == 'Test log message':
            found_msg = True
            break
    assert found_msg


def test_get_logs_file_not_found(test_client, mocker):
    admin_token = create_access_token(identity="admin@example.com")
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', "admin@example.com")

    mocker.patch('builtins.open', side_effect=FileNotFoundError)

    response = test_client.get(
        '/api/admin/logs', headers={'Authorization': f'Bearer {admin_token}'})
    assert response.status_code == 404
    assert response.json['error'] == 'Log file not found.'


def test_get_status_success(test_client, mocker):
    admin_token = create_access_token(identity="admin@example.com")
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', "admin@example.com")

    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "tunnels": [
            {"proto": "tcp", "public_url": "tcp://0.tcp.ngrok.io:12345"}
        ]
    }
    mock_response.raise_for_status.return_value = None
    mocker.patch('gabs_api_server.app.requests.get',
                 return_value=mock_response)

    response = test_client.get(
        '/api/admin/status',
        headers={
            'Authorization': f'Bearer {admin_token}'})
    assert response.status_code == 200
    assert response.json['status'] == 'ok'
    assert "ssh -p 12345 gabs-admin@0.tcp.ngrok.io" in response.json['ssh_tunnel_command']


def test_get_status_no_tcp_tunnel(test_client, mocker):
    admin_token = create_access_token(identity="admin@example.com")
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', "admin@example.com")

    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "tunnels": [
            {"proto": "http", "public_url": "https://example.ngrok-free.app"}
        ]
    }
    mock_response.raise_for_status.return_value = None
    mocker.patch('gabs_api_server.app.requests.get',
                 return_value=mock_response)

    response = test_client.get(
        '/api/admin/status',
        headers={
            'Authorization': f'Bearer {admin_token}'})
    assert response.status_code == 200
    assert response.json['status'] == 'ok'
    assert response.json['ssh_tunnel_command'] is None


def test_get_status_ngrok_error(test_client, mocker):
    admin_token = create_access_token(identity="admin@example.com")
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', "admin@example.com")

    # Raise RequestException specifically, as caught in app.py
    mocker.patch(
        'gabs_api_server.app.requests.get',
        side_effect=requests.exceptions.RequestException("Connection failed"))
    mock_logging_error = mocker.patch('gabs_api_server.app.logging.error')

    response = test_client.get(
        '/api/admin/status',
        headers={
            'Authorization': f'Bearer {admin_token}'})
    assert response.status_code == 200
    assert response.json['status'] == 'ok'
    assert response.json['ssh_tunnel_command'] is None
    mock_logging_error.assert_called_once()
