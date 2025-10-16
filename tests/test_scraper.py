import pytest
from datetime import date, datetime
from scraper import Scraper

def test_parse_classes_from_html(mocker):
    # Mock the __init__ method of the Scraper class to prevent the login attempt
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()

    sample_html = '''
    <div class="class grid">
        <h2 class="title">Test Class 1</h2>
        <div class="description">Test Description 1</div>
        <p>with Test Instructor 1</p>
        <span itemprop="startDate">10:00</span>
        <span itemprop="endDate">11:00</span>
        <span class="remaining">5</span>
    </div>
    <div class="class grid">
        <h2 class="title">Test Class 2</h2>
        <div class="description">Test Description 2</div>
        <p>with Test Instructor 2.</p>
        <span itemprop="startDate">12:00</span>
        <span itemprop="endDate">13:00</span>
        <span class="remaining">10</span>
    </div>
    '''
    target_date = date(2025, 10, 26)

    parsed_classes = scraper_instance._parse_classes_from_html(sample_html, target_date)

    expected_classes = [
        {
            'name': 'Test Class 1',
            'description': 'Test Description 1',
            'instructor': 'Test Instructor 1',
            'date': '26/10/2025',
            'start_time': '10:00',
            'end_time': '11:00',
            'duration': 60,
            'available_spaces': 5
        },
        {
            'name': 'Test Class 2',
            'description': 'Test Description 2',
            'instructor': 'Test Instructor 2',
            'date': '26/10/2025',
            'start_time': '12:00',
            'end_time': '13:00',
            'duration': 60,
            'available_spaces': 10
        }
    ]

    assert parsed_classes == expected_classes

def test_get_csrf_token_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()

    mock_response = mocker.Mock()
    mock_response.text = '''
    <html>
        <head>
            <meta name="csrf-token" content="test_token">
        </head>
        <body></body>
    </html>
    '''
    mock_response.raise_for_status.return_value = None
    scraper_instance.session.get.return_value = mock_response

    token = scraper_instance._get_csrf_token()

    assert token == "test_token"

def test_get_csrf_token_failure(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()

    import requests
    scraper_instance.session.get.side_effect = requests.exceptions.RequestException("Network Error")

    token = scraper_instance._get_csrf_token()

    assert token is None

def test_login_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()
    scraper_instance.username = "test_user"
    scraper_instance.password = "test_pass"
    scraper_instance.relogin_failures = 0
    scraper_instance.disabled_until = None

    mocker.patch.object(scraper_instance, '_get_csrf_token', return_value="test_token")
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"X_WINTER_REDIRECT": "/members"}
    mock_response.raise_for_status.return_value = None
    scraper_instance.session.post.return_value = mock_response

    assert scraper_instance._login() is True

def test_login_failure(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()
    scraper_instance.username = "test_user"
    scraper_instance.password = "test_pass"
    scraper_instance.relogin_failures = 0
    scraper_instance.disabled_until = None

    mocker.patch.object(scraper_instance, '_get_csrf_token', return_value="test_token")
    mock_response = mocker.Mock()
    mock_response.json.return_value = {}
    mock_response.raise_for_status.return_value = None
    scraper_instance.session.post.return_value = mock_response

    assert scraper_instance._login() is False

def test_get_classes_for_single_date_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()
    scraper_instance.csrf_token = "test_token"
    scraper_instance.disabled_until = None

    mock_response = mocker.Mock()
    mock_response.json.return_value = {"@events": "<p>some html</p>"}
    mock_response.raise_for_status.return_value = None
    scraper_instance.session.post.return_value = mock_response

    html = scraper_instance._get_classes_for_single_date("2025-10-26")

    assert html == "<p>some html</p>"

def test_get_classes_for_single_date_relogin(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()
    scraper_instance.csrf_token = "test_token"
    scraper_instance.disabled_until = None

    mock_response1 = mocker.Mock()
    mock_response1.json.return_value = {"X_OCTOBER_REDIRECT": "/login"}
    mock_response1.raise_for_status.return_value = None

    mock_response2 = mocker.Mock()
    mock_response2.json.return_value = {"@events": "<p>some html after relogin</p>"}
    mock_response2.raise_for_status.return_value = None

    scraper_instance.session.post.side_effect=[mock_response1, mock_response2]
    mocker.patch.object(scraper_instance, '_login', return_value=True)

    html = scraper_instance._get_classes_for_single_date("2025-10-26")

    assert html == "<p>some html after relogin</p>"
    assert scraper_instance._login.call_count == 1

def test_parse_and_execute_booking_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()
    scraper_instance.csrf_token = "test_token"

    sample_html = '''
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
        <form data-request="onBook">
            <input name="id" value="123">
            <input name="timestamp" value="456">
            <button type="submit" class="signup"></button>
        </form>
    </div>
    '''
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"status": "success"}
    mock_response.text = '{"status": "success"}'
    scraper_instance.session.post.return_value = mock_response

    result = scraper_instance._parse_and_execute_booking(sample_html, "Test Class", "10:00", "")

    assert result['status'] == 'success'

def test_parse_and_execute_booking_already_booked(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()

    sample_html = '''
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
        you are already registered
    </div>
    '''

    result = scraper_instance._parse_and_execute_booking(sample_html, "Test Class", "10:00", "")

    assert result['status'] == 'info'
    assert "already registered" in result['message']

def test_parse_and_execute_booking_no_form(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()

    sample_html = '''
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span itemprop="startDate">10:00</span>
    </div>
    '''

    result = scraper_instance._parse_and_execute_booking(sample_html, "Test Class", "10:00", "")

    assert result['status'] == 'error'
    assert "no booking form" in result['message']

def test_parse_and_execute_booking_not_found(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()

    sample_html = '''
    <div class="class grid">
        <h2 class="title">Another Class</h2>
        <span itemprop="startDate">12:00</span>
    </div>
    '''

    result = scraper_instance._parse_and_execute_booking(sample_html, "Test Class", "10:00", "")

    assert result['status'] == 'error'
    assert "Could not find a suitable match" in result['message']

def test_find_and_cancel_booking_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    mocker.patch.object(scraper_instance, '_get_classes_for_single_date', return_value="<p>some html</p>")
    mocker.patch.object(scraper_instance, '_parse_and_execute_cancellation', return_value={"status": "success"})

    result = scraper_instance.find_and_cancel_booking("Test Class", "2025-10-26", "10:00")

    assert result['status'] == 'success'

def test_get_my_bookings_success(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper_instance = Scraper()
    scraper_instance.session = mocker.Mock()

    sample_html = '''
    <div id="upcoming_bookings">
        <li>Test Class 1 - Monday 27th October 10:00</li>
        <li><strong>WAITINGLIST</strong>Test Class 2 - Tuesday 28th October 12:00</li>
    </div>
    '''
    mock_response = mocker.Mock()
    mock_response.text = sample_html
    scraper_instance.session.get.return_value = mock_response

    bookings = scraper_instance.get_my_bookings()

    expected_bookings = [
        {
            'name': 'Test Class 1',
            'date': 'Monday 27th October',
            'time': '10:00',
            'status': 'Booked'
        },
        {
            'name': 'Test Class 2',
            'date': 'Tuesday 28th October',
            'time': '12:00',
            'status': 'Waiting List'
        }
    ]

    assert bookings == expected_bookings
