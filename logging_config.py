import logging
from logging.handlers import RotatingFileHandler
import sys

try:
    from .task_logger import TaskContextFilter, JSONFormatter, HumanReadableFormatter
except ImportError:
    from task_logger import TaskContextFilter, JSONFormatter, HumanReadableFormatter

LOG_FILE = 'gabs_api.log'


class NoCancellationFilter(logging.Filter):
    def filter(self, record):
        return "Running send_cancellation_reminders job" not in record.getMessage()


def setup_logging():
    """
    Configures the root logger for the application.
    This function is idempotent and can be called multiple times.
    """
    root_logger = logging.getLogger()
    # Clear existing handlers to avoid duplicate logs
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    # Task context filter — injects task_id, scenario, user, etc.
    task_filter = TaskContextFilter()

    # File handler — JSON Lines format (machine-readable)
    json_formatter = JSONFormatter()
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1024 * 1024 * 5, backupCount=2)
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(NoCancellationFilter())
    file_handler.addFilter(task_filter)

    # Console handler — human-readable format
    console_formatter = HumanReadableFormatter()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(NoCancellationFilter())
    console_handler.addFilter(task_filter)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger('apscheduler').setLevel(logging.WARNING)
