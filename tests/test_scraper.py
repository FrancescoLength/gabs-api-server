import pytest
from scraper import Scraper

@pytest.fixture
def scraper_mock(mocker):
    mocker.patch.object(Scraper, '__init__', return_value=None)
    scraper = Scraper()
    scraper.session = mocker.Mock()
    scraper.username = "test_user"
    scraper.base_headers = {}
    scraper.csrf_token = "test_token"
    scraper.user_agent = "test_agent"
    mocker.patch.object(scraper, '_get_csrf_token', return_value="test_token")
    return scraper

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
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'status': 'success', 'class_name': 'Test Class'}
    mock_response.text = "" # Add this line
    scraper_mock.session.post.return_value = mock_response
    
    result = scraper_mock.find_and_book_class("2025-01-01", "Test Class", "10:00", "Test Instructor")
    
    assert result['status'] == 'success'
    scraper_mock.session.post.assert_called_once()

def test_get_class_availability_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Test Class</h2>
        <span class="remaining">5</span>
    </div>
    """
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    
    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")
    
    assert result['remaining_spaces'] == 5

def test_get_class_availability_not_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Another Class</h2>
    </div>
    """
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value={"@events": html})
    
    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")
    
    assert "not found" in result['error']

import json

def test_find_and_book_class_no_events_html(scraper_mock, mocker):
    # Simula una risposta JSON senza il campo '@events'
    json_response_without_events = {"some_other_field": "value"}
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value=json_response_without_events)
    
    result = scraper_mock.find_and_book_class("2025-01-01", "Test Class", "10:00", "Test Instructor")
    
    assert result['status'] == 'error'
    assert "Could not retrieve class list HTML" in result['message']
    assert result['html_content'] == json.dumps(json_response_without_events, indent=2)

def test_find_and_book_class_match_not_found(scraper_mock, mocker):
    # Simula una risposta JSON con HTML che non contiene la classe "Test Class"
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
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value=json_response_with_html)
    
    result = scraper_mock.find_and_book_class("2025-01-01", "Test Class", "10:00", "Test Instructor")
    
    assert result['status'] == 'error'
    assert "Could not find a suitable match for 'Test Class' at 10:00" in result['message']
    assert result['html_content'] == html_with_no_match
