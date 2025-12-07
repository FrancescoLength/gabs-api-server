import pytest
import signal
import threading
import sys
from gabs_api_server import scheduler_runner

def test_run_scheduler_loop(mocker):
    # Mock dependencies
    mock_scheduler_class = mocker.patch('gabs_api_server.scheduler_runner.BackgroundScheduler')
    mock_scheduler_instance = mock_scheduler_class.return_value
    mocker.patch('gabs_api_server.scheduler_runner.signal.signal')
    
    # Simulate loop running once then raising KeyboardInterrupt to exit the while True loop
    mocker.patch('gabs_api_server.scheduler_runner.time.sleep', side_effect=KeyboardInterrupt)
    
    mock_graceful_shutdown = mocker.patch('gabs_api_server.scheduler_runner.graceful_shutdown')

    # Run the scheduler
    scheduler_runner.run_scheduler()

    # Assertions
    mock_scheduler_instance.start.assert_called_once()
    # Check that jobs were added (at least one)
    assert mock_scheduler_instance.add_job.call_count >= 4
    
    # Verify graceful_shutdown was called upon KeyboardInterrupt
    mock_graceful_shutdown.assert_called_with(signal.SIGINT, None)

def test_graceful_shutdown_handler(mocker):
    # Mock sys.exit
    mock_exit = mocker.patch('sys.exit')
    
    # Mock the global scheduler object in scheduler_runner
    mock_scheduler = mocker.Mock()
    scheduler_runner.scheduler = mock_scheduler
    
    mocker.patch('gabs_api_server.scheduler_runner.logging.info')

    # Call the handler directly
    scheduler_runner.graceful_shutdown(signal.SIGINT, None)

    # Assertions
    mock_scheduler.shutdown.assert_called_once()
    mock_exit.assert_called_once_with(0)

def test_graceful_shutdown_handler_no_scheduler(mocker):
    # Mock sys.exit
    mock_exit = mocker.patch('sys.exit')
    
    # Ensure global scheduler is None
    scheduler_runner.scheduler = None
    
    mocker.patch('gabs_api_server.scheduler_runner.logging.info')

    # Call the handler directly
    scheduler_runner.graceful_shutdown(signal.SIGINT, None)

    # Assertions
    # Should not raise error and simply exit
    mock_exit.assert_called_once_with(0)