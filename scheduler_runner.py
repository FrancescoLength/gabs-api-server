import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import time

# Import the app object and job functions from the main application file.
from app import app, process_auto_bookings, send_cancellation_reminders, reset_failed_bookings
import database

from logging_config import setup_logging

# Configure logging
setup_logging()

if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.info("Starting standalone scheduler process...")

    jobstores = {
        'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}')
    }
    
    # Using a ThreadPoolExecutor to handle concurrent jobs.
    # This allows multiple booking jobs to run in parallel, 
    # preventing one user's attempt from blocking another's.
    executors = {
        'default': ThreadPoolExecutor(10)
    }

    scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors)

    # Add jobs to the scheduler
    scheduler.add_job(process_auto_bookings, 'interval', minutes=1, id='auto_booking_processor', replace_existing=True)
    scheduler.add_job(send_cancellation_reminders, 'interval', minutes=1, id='cancellation_reminder_sender', replace_existing=True)
    scheduler.add_job(reset_failed_bookings, 'interval', hours=24, id='reset_failed_bookings_job', replace_existing=True)
    
    scheduler.start()
    logger.info("Scheduler started and running. Press Ctrl+C to exit.")

    try:
        # Keep the main thread alive, otherwise the script will exit.
        while True:
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Scheduler shut down.")
