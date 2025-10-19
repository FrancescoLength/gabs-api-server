import logging
from logging.handlers import RotatingFileHandler
import sys

LOG_FILE = 'gabs_api.log'

def setup_logging():
    """
    Configures the root logger for the application.
    This function is idempotent and can be called multiple times.
    """
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        return

    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # File handler
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1024 * 1024 * 5, backupCount=2)
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger('apscheduler').setLevel(logging.WARNING)
