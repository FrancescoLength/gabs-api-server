import pytest
from flask import jsonify, Flask
from gabs_api_server.app import app, limiter, get_scraper_instance, handle_session_expiration, admin_required
from gabs_api_server.scraper import Scraper
from flask_jwt_extended import create_access_token, JWTManager


@pytest.fixture
def test_app_client():
    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret-key"
    limiter.enabled = False  # Ensure limiter is disabled for unit tests
    with app.test_client() as client:
        yield client


def test_get_scraper_instance_with_password_success(mocker):
    # Mock Scraper and its methods
    mock_scraper_instance = mocker.Mock(spec=Scraper)
    mock_scraper_instance.to_dict.return_value = {"session": "data"}
    # Patch the Scraper class and store the patch object
    mock_scraper_class = mocker.patch(
        'gabs_api_server.app.Scraper', return_value=mock_scraper_instance)

    # Mock database and crypto functions
    mocker.patch('gabs_api_server.app.database.load_session',
                 return_value=(None, None))
    # Patch crypto.encrypt and store the patch object
    mock_crypto_encrypt = mocker.patch(
        'gabs_api_server.app.crypto.encrypt', return_value="encrypted_password")
    # Patch database.save_session and store the patch object
    mock_database_save_session = mocker.patch(
        'gabs_api_server.app.database.save_session')

    scraper = get_scraper_instance("test_user", "test_password")

    assert scraper is mock_scraper_instance
    mock_scraper_class.assert_called_once_with("test_user", "test_password")
    # Correctly assert on mock_crypto_encrypt
    mock_crypto_encrypt.assert_called_once_with("test_password")
    mock_database_save_session.assert_called_once_with(
        "test_user", "encrypted_password", {"session": "data"})


def test_get_scraper_instance_with_password_failure(mocker):
    # Mock Scraper to raise an exception during initialization
    mocker.patch('gabs_api_server.app.Scraper',
                 side_effect=Exception("Scraper init failed"))

    # Mock database and crypto functions
    mocker.patch('gabs_api_server.app.database.load_session',
                 return_value=(None, None))
    mock_logging_error = mocker.patch(
        'gabs_api_server.app.logging.error')  # Patch the specific logger

    scraper = get_scraper_instance("test_user", "test_password")

    assert scraper is None
    mock_logging_error.assert_called_once()
    assert "Failed to create new session" in mock_logging_error.call_args[0][0]


def test_get_scraper_instance_without_password_existing_session_success(mocker):
    # Mock Scraper and its methods
    mock_scraper_instance = mocker.Mock(spec=Scraper)
    # Patch the Scraper class and store the patch object
    mock_scraper_class = mocker.patch(
        'gabs_api_server.app.Scraper', return_value=mock_scraper_instance)

    # Mock database and crypto functions
    mocker.patch('gabs_api_server.app.database.load_session',
                 return_value=("encrypted_password", {"cookies": "data"}))
    # Patch crypto.decrypt and store the patch object
    mock_crypto_decrypt = mocker.patch(
        'gabs_api_server.app.crypto.decrypt', return_value="plain_password")

    scraper = get_scraper_instance("test_user")

    assert scraper is mock_scraper_instance
    mock_crypto_decrypt.assert_called_once_with("encrypted_password")
    mock_scraper_class.assert_called_once_with(
        "test_user", "plain_password", session_data={"cookies": "data"})


def test_get_scraper_instance_without_password_existing_session_failure(mocker):
    # Mock Scraper to raise an exception during initialization
    mocker.patch('gabs_api_server.app.Scraper',
                 side_effect=Exception("Scraper init failed"))

    # Mock database and crypto functions
    mocker.patch('gabs_api_server.app.database.load_session',
                 return_value=("encrypted_password", {"cookies": "data"}))
    mocker.patch('gabs_api_server.app.crypto.decrypt',
                 return_value="plain_password")
    mock_logging_error = mocker.patch(
        'gabs_api_server.app.logging.error')  # Patch the specific logger

    scraper = get_scraper_instance("test_user")

    assert scraper is None
    mock_logging_error.assert_called_once()
    assert "Failed to restore session" in mock_logging_error.call_args[0][0]


def test_get_scraper_instance_no_password_no_session(mocker):
    # Mock database to return no session
    mocker.patch('gabs_api_server.app.database.load_session',
                 return_value=(None, None))
    mock_logging_warning = mocker.patch(
        'gabs_api_server.app.logging.warning')  # Patch the specific logger

    scraper = get_scraper_instance("test_user")

    assert scraper is None
    mock_logging_warning.assert_called_once()
    assert "No session or credentials found" in mock_logging_warning.call_args[0][0]


def test_handle_session_expiration(mocker):
    mock_logging_warning = mocker.patch(
        'gabs_api_server.app.logging.warning')  # Patch the specific logger
    handle_session_expiration("test_user")
    mock_logging_warning.assert_called_once_with(
        "Session for test_user has expired. A proactive refresh or user login is required."
    )


def test_health_check_endpoint(test_app_client):
    response = test_app_client.get('/api/health')
    assert response.status_code == 200
    assert response.json['status'] == 'ok'
    assert 'uptime' in response.json


def test_get_static_classes_success(test_app_client, mocker):
    mock_static_data = {"class1": "details"}
    mocker.patch('gabs_api_server.app.os.path.exists', return_value=True)
    mocker.patch('gabs_api_server.app.json.load',
                 return_value=mock_static_data)
    mocker.patch('builtins.open', mocker.mock_open(
        read_data='{"class1": "details"}'))

    response = test_app_client.get('/api/static_classes')
    assert response.status_code == 200
    assert response.json == mock_static_data


def test_get_static_classes_file_not_found(test_app_client, mocker):
    mocker.patch('gabs_api_server.app.os.path.exists', return_value=False)
    mock_logging_warning = mocker.patch(
        'gabs_api_server.app.logging.warning')  # Patch the specific logger

    response = test_app_client.get('/api/static_classes')
    assert response.status_code == 404
    assert response.json['error'] == 'Static timetable not found.'
    mock_logging_warning.assert_called_once()
    assert "Static timetable file not found" in mock_logging_warning.call_args[0][0]


@pytest.fixture
def temp_flask_app_for_decorator_test():
    _app = Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["JWT_SECRET_KEY"] = "test-secret-key"
    _app.config["ADMIN_EMAIL"] = "admin@example.com"
    _app.config["JWT_TOKEN_LOCATION"] = ["headers"]
    _app.config["JWT_HEADER_NAME"] = "Authorization"
    _app.config["JWT_HEADER_TYPE"] = "Bearer"  # <--- Add this line
    JWTManager(_app)  # Initialize JWTManager with the temporary app
    return _app


def test_admin_required_decorator_admin_access(temp_flask_app_for_decorator_test, mocker):
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', 'admin@example.com')

    with temp_flask_app_for_decorator_test.app_context():  # Push app context
        admin_token = create_access_token(identity="admin@example.com")

    @temp_flask_app_for_decorator_test.route('/test_admin_route')
    @admin_required
    def test_route():
        return jsonify({"message": "Admin granted"})

    with temp_flask_app_for_decorator_test.test_client() as client:
        response = client.get(
            '/test_admin_route', headers={'Authorization': f'Bearer {admin_token}'})
        assert response.status_code == 200
        assert response.json['message'] == 'Admin granted'


def test_admin_required_decorator_user_access(temp_flask_app_for_decorator_test, mocker):
    mocker.patch('gabs_api_server.app.config.ADMIN_EMAIL', 'admin@example.com')

    with temp_flask_app_for_decorator_test.app_context():  # Push app context
        user_token = create_access_token(identity="user@example.com")

    @temp_flask_app_for_decorator_test.route('/test_admin_route_user')
    @admin_required
    def test_route_user():
        return jsonify({"message": "Admin granted"})

    with temp_flask_app_for_decorator_test.test_client() as client:
        response = client.get('/test_admin_route_user',
                              headers={'Authorization': f'Bearer {user_token}'})
        assert response.status_code == 403
        assert response.json['error'] == 'Admins only!'
