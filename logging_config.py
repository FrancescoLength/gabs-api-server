import logging
from logging.handlers import RotatingFileHandler
import sys

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

    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s')

    # File handler
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1024 * 1024 * 5, backupCount=2)
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(NoCancellationFilter())

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(NoCancellationFilter())

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger('apscheduler').setLevel(logging.WARNING)
