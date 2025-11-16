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
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value=html)
    
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
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value=html)
    
    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")
    
    assert result['remaining_spaces'] == 5

def test_get_class_availability_not_found(scraper_mock, mocker):
    html = """
    <div class="class grid">
        <h2 class="title">Another Class</h2>
    </div>
    """
    mocker.patch.object(scraper_mock, '_get_classes_for_single_date', return_value=html)
    
    result = scraper_mock.get_class_availability("Test Class", "2025-01-01")
    
    assert "not found" in result['error']
