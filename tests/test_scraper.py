import pytest
import requests
from unittest.mock import Mock, MagicMock
from datetime import datetime, timedelta, date
from gabs_api_server.scraper import Scraper, SessionExpiredError, handle_session_expiry

# --- Fixtures ---


@pytest.fixture
def scraper_mock(mocker):
    """Provides a Scraper instance with mocked networking and initialization."""
    # Mock __init__ to skip the initial login call during instantiation
    mocker.patch.object(Scraper, '__init__', return_value=None)

    scraper = Scraper()
    scraper.username = "test_user"
    scraper.password = "test_password"
    scraper.session = mocker.Mock(spec=requests.Session)
    scraper.base_headers = {'User-Agent': 'test-agent'}
    scraper.csrf_token = "test_token"
    scraper.user_agent = "test_agent"
    scraper.relogin_failures = 0
    scraper.disabled_until = None

    return scraper

# --- Tests for __init__ ---


def test_init_success(mocker):
    mocker.patch.object(Scraper, '_login', return_value=True)
    scraper = Scraper("user", "pass")
    assert scraper.username == "user"
    assert scraper.password == "pass"
    assert scraper.session is not None


def test_init_failure(mocker):
    mocker.patch.object(Scraper, '_login', return_value=False)
    with pytest.raises(Exception, match="Initial login failed"):
        Scraper("user", "pass")


def test_init_with_session_data(mocker):
    session_data = {'cookies': {'cookie': 'yum'}, 'csrf_token': 'token'}
    # from_dict will be called, bypassing _login
    scraper = Scraper("user", "pass", session_data=session_data)
    assert scraper.csrf_token == 'token'
    assert scraper.session.cookies.get('cookie') == 'yum'

# --- Tests for _get_csrf_token ---


def test_get_csrf_token_success(scraper_mock, mocker):
    html = '<html><head><meta name="csrf-token" content="new_token"></head></html>'
    mock_response = MagicMock()
    mock_response.text = html
    scraper_mock.session.get.return_value = mock_response

    token = scraper_mock._get_csrf_token()
    assert token == "new_token"


def test_get_csrf_token_tag_dict_behavior(scraper_mock, mocker):
    # Test the branch where token_tag behaves like a dict (BeautifulSoup Tag)
    html = '<html><head><meta name="csrf-token" content="new_token"></head></html>'
    mock_response = MagicMock()
    mock_response.text = html
    scraper_mock.session.get.return_value = mock_response

    # Mock BeautifulSoup to return a tag that is a dict but not strictly an instance of dict?
    # Actually, the code `isinstance(token_tag, dict)` checks if it's a dict.
    # BS4 Tags act like dicts but aren't dict instances.
    # The code `if token_tag and isinstance(token_tag, dict):` is likely checking for raw dicts
    # (maybe from JSON parsing mocked elsewhere?).
    # The real BS4 tag falls into `elif token_tag:`.

    # Let's try to hit the `elif token_tag:` branch more explicitly if needed,
    # but the standard test hits it if `isinstance(tag, dict)` is false.
    # Standard BS4 tag is NOT a dict instance.

    token = scraper_mock._get_csrf_token()
    assert token == "new_token"


def test_get_csrf_token_failure(scraper_mock, mocker):
    html = '<html><head></head></html>'
    mock_response = MagicMock()
    mock_response.text = html
    scraper_mock.session.get.return_value = mock_response

    token = scraper_mock._get_csrf_token()
    assert token is None


def test_get_csrf_token_exception(scraper_mock, mocker):
    scraper_mock.session.get.side_effect = requests.exceptions.RequestException(
        "Net error")
    mock_logger_error = mocker.patch('gabs_api_server.scraper.logging.error')

    token = scraper_mock._get_csrf_token()
    assert token is None
    mock_logger_error.assert_called()

# --- Tests for _login ---


def test_login_success(scraper_mock, mocker):
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh_token")

    mock_response = MagicMock()
    mock_response.json.return_value = {"X_WINTER_REDIRECT": "http://dashboard"}
    scraper_mock.session.post.return_value = mock_response

    result = scraper_mock._login()
    assert result is True
    assert scraper_mock.csrf_token == "fresh_token"
    assert scraper_mock.relogin_failures == 0


def test_login_disabled(scraper_mock):
    scraper_mock.disabled_until = datetime.now() + timedelta(minutes=10)
    assert scraper_mock._login() is False


def test_login_failure_credentials(scraper_mock, mocker):
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh_token")

    mock_response = MagicMock()
    mock_response.json.return_value = {}  # No redirect
    mock_response.text = "Invalid login"
    scraper_mock.session.post.return_value = mock_response

    with pytest.raises(Exception, match="Login failed"):
        scraper_mock._login()


def test_login_http_error(scraper_mock, mocker):
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh_token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.request.url = "http://base/login"
    error = requests.exceptions.HTTPError(
        "Server Error", response=mock_response)

    scraper_mock.session.post.side_effect = error

    result = scraper_mock._login()
    assert result is False
    assert scraper_mock.relogin_failures == 1

# --- Tests for get_classes ---


def test_get_classes_parsing(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Yoga</h2>
        <div class="description">Relaxing flow</div>
        <span itemprop="startDate">10:00</span>
        <span itemprop="endDate">11:00</span>
        <p>with Yogi Master</p>
        <span class="remaining">10</span>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})

    classes = scraper_mock.get_classes(days_in_advance=1)

    assert len(classes) == 1
    c = classes[0]
    assert c['name'] == "Yoga"
    assert c['instructor'] == "Yogi Master"
    assert c['duration'] == 60
    assert c['available_spaces'] == 10


def test_parse_classes_variations(scraper_mock):
    # Test day wrapping logic (end time < start time) and missing fields
    target_date = date(2025, 1, 1)
    html = """
    <div class="class grid">
        <h2 class="title">Late Night Yoga</h2>
        <span itemprop="startDate">23:30</span>
        <span itemprop="endDate">00:30</span>
        <span class="remaining">Full</span>
    </div>
    """
    classes = scraper_mock._parse_classes_from_html(html, target_date)
    assert len(classes) == 1
    c = classes[0]
    assert c['name'] == "Late Night Yoga"
    assert c['duration'] == 60  # Should handle wrap around
    assert c['available_spaces'] == 0  # "Full" is not digit

# --- Tests for find_and_book_class ---


def test_find_and_book_class_success(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <p>with Test Instructor</p>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <input name="id" value="123">
            <input name="timestamp" value="456">
            <button type="submit" class="signup"></button>
        </form>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token', return_value="fresh")

    mock_response = MagicMock()
    mock_response.json.return_value = {'status': 'success'}
    mock_response.text = "success"
    scraper_mock.session.post.return_value = mock_response

    result = scraper_mock.find_and_book_class(
        "2025-01-01", "Test Class", "10:00", "Test Instructor")

    assert result['status'] == 'success'
    assert result['class_name'] == "Test Class"


def test_find_and_book_class_waitlist(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <input name="id" value="123">
            <input name="timestamp" value="456">
            <button type="submit" class="waitinglist"></button>
        </form>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token', return_value="fresh")

    mock_response = MagicMock()
    mock_response.json.return_value = {'status': 'success'}
    mock_response.text = "success"
    scraper_mock.session.post.return_value = mock_response

    result = scraper_mock.find_and_book_class(
        "2025-01-01", "Test Class", "10:00")

    assert result['status'] == 'success'
    assert result['action'] == 'waitlisting'


def test_find_and_book_class_already_booked(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
        You are already registered
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh")  # Added mock

    result = scraper_mock.find_and_book_class(
        "2025-01-01", "Test Class", "10:00")

    assert result['status'] == 'info'
    assert "already registered" in result['message']


def test_find_and_book_class_session_expired_redirect(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <input name="id" value="123">
            <input name="timestamp" value="456">
            <button type="submit" class="signup"></button>
        </form>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token', return_value="fresh")

    mock_response = MagicMock()
    mock_response.text = "X_OCTOBER_REDIRECT"
    scraper_mock.session.post.return_value = mock_response

    with pytest.raises(SessionExpiredError):
        scraper_mock.find_and_book_class("2025-01-01", "Test Class", "10:00")

# --- Tests for find_and_cancel_booking ---


def test_find_and_cancel_booking_success(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Cancel Me</h2>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <input name="id" value="999">
            <input name="timestamp" value="888">
            <button class="cancel">Cancel</button>
        </form>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token', return_value="fresh")

    mock_response = MagicMock()
    mock_response.json.return_value = {'status': 'success'}
    mock_response.text = "success"
    scraper_mock.session.post.return_value = mock_response

    result = scraper_mock.find_and_cancel_booking(
        "Cancel Me", "2025-01-01", "10:00")

    assert result['status'] == 'success'
    assert result['action'] == 'cancellation'


def test_find_and_cancel_booking_not_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Keep Me</h2>
        <span itemprop="startDate">12:00</span>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh")  # Added mock

    result = scraper_mock.find_and_cancel_booking(
        "Cancel Me", "2025-01-01", "10:00")

    assert result['status'] == 'error'
    assert "not found" in result['message']


def test_find_and_cancel_booking_no_button(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Cancel Me</h2>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <button class="signup">Sign Up</button>
        </form>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh")  # Added mock

    result = scraper_mock.find_and_cancel_booking(
        "Cancel Me", "2025-01-01", "10:00")

    assert result['status'] == 'error'
    assert "cancellation is not possible" in result['message']

# --- Tests for get_my_bookings ---


def test_get_my_bookings_success(scraper_mock, mocker):
    html = """
    <div id="upcoming_bookings">
        <li>Yoga - 01/01/2025 10:00</li>
        <li><strong>WAITINGLIST</strong> Boxing - 02/01/2025 18:00</li>
    </div>
    """
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.url = "http://base/members"
    scraper_mock.session.get.return_value = mock_response

    bookings = scraper_mock.get_my_bookings()

    assert len(bookings) == 2
    assert bookings[0]['name'] == "Yoga"
    assert bookings[0]['status'] == "Booked"
    assert bookings[1]['name'] == "Boxing"
    assert bookings[1]['status'] == "Waiting List"


def test_get_my_bookings_redirect_login(scraper_mock, mocker):
    # Mock LOGIN_URL to match our mock response URL
    mocker.patch('gabs_api_server.scraper.LOGIN_URL', "http://base/login")

    mock_response = MagicMock()
    mock_response.url = "http://base/login"  # Redirected
    mock_response.text = "<html>Login Page</html>"  # Set text to avoid BS4 TypeError
    scraper_mock.session.get.return_value = mock_response

    # Mock _login so handle_session_expiry can call it
    # Login fails so exception propagates
    mocker.patch.object(scraper_mock, '_login', return_value=False)

    with pytest.raises(SessionExpiredError):
        scraper_mock.get_my_bookings()


def test_get_my_bookings_empty_container(scraper_mock, mocker):
    html = """
    <html>
        <body>No bookings container here</body>
    </html>
    """
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.url = "http://base/members"
    scraper_mock.session.get.return_value = mock_response

    bookings = scraper_mock.get_my_bookings()
    assert len(bookings) == 0


def test_get_my_bookings_parsing_error(scraper_mock, mocker):
    html = """
    <div id="upcoming_bookings">
        <li>Unparseable String Here</li>
    </div>
    """
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.url = "http://base/members"
    scraper_mock.session.get.return_value = mock_response

    # Should log warning and continue
    mock_logger_warning = mocker.patch(
        'gabs_api_server.scraper.logging.warning')

    bookings = scraper_mock.get_my_bookings()

    assert len(bookings) == 0
    mock_logger_warning.assert_called_with(
        "Could not parse booking string: Unparseable String Here")

# --- Tests for Decorator ---


def test_handle_session_expiry_relogin_success(scraper_mock, mocker):
    # Function that raises session expired once, then succeeds
    mock_func = Mock(side_effect=[SessionExpiredError("Expired"), "Success"])
    mock_func.__name__ = "mock_func"  # Set name

    # Mock login to succeed
    mocker.patch.object(scraper_mock, '_login', return_value=True)

    # Wrap it
    wrapped = handle_session_expiry(mock_func)

    # Call it
    result = wrapped(scraper_mock)

    assert result == "Success"
    assert mock_func.call_count == 2
    scraper_mock._login.assert_called_once()


def test_handle_session_expiry_relogin_fail(scraper_mock, mocker):
    mock_func = Mock(side_effect=SessionExpiredError("Expired"))
    mock_func.__name__ = "mock_func"  # Set name

    mocker.patch.object(scraper_mock, '_login', return_value=False)

    wrapped = handle_session_expiry(mock_func)

    with pytest.raises(SessionExpiredError) as excinfo:
        wrapped(scraper_mock)

    assert "Automatic re-login failed" in str(excinfo.value)

# --- Existing Tests (Consolidated) ---


def test_get_class_availability_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span class="remaining">5</span>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})

    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")

    assert result['remaining_spaces'] == 5


def test_get_class_availability_not_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Another Class</h2>
    </div>
    """
    mocker.patch.object(
        scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})

    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")

    assert "not found" in result['error']


def test_find_and_book_class_no_events_html(scraper_mock, mocker):
    json_response_without_events = {"some_other_field": "value"}
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date',
                        return_value=json_response_without_events)

    result = scraper_mock.find_and_book_class(
        "2025-01-01", "Test Class", "10:00", "Test Instructor")

    assert result['status'] == 'error'
    assert "Could not retrieve class list HTML" in result['message']


def test_find_and_book_class_match_not_found(scraper_mock, mocker):
    html_with_no_match = """
    <div class="class grid">
        <h2 class="title">Another Class</h2>
        <p>with Another Instructor</p>
        <span itemprop="startDate">11:00</span>
        <form data-request="onBook">
            <input name="id" value="789">
            <input name="timestamp" value="101">
            <button type="submit" class="signup"></button>
        </form>
    </div>
    """
    json_response_with_html = {"@events": html_with_no_match}
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date',
                        return_value=json_response_with_html)
    mocker.patch.object(scraper_mock, '_get_csrf_token',
                        return_value="fresh")  # Added mock here too

    result = scraper_mock.find_and_book_class(
        "2025-01-01", "Test Class", "10:00", "Test Instructor")

    assert result['status'] == 'error'
    assert "Could not find a suitable match" in result['message']
