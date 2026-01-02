import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import time
import signal
import sys
from typing import Dict

try:
    from .app import (
        send_cancellation_reminders, reset_failed_bookings, refresh_sessions, app,
        debug_writer_queue, get_scraper_instance, handle_session_expiration
    )
    from . import database
    from .services.auto_booking_service import process_auto_bookings_job
    from .logging_config import setup_logging
except ImportError:
    from app import (
        send_cancellation_reminders, reset_failed_bookings, refresh_sessions, app,
        debug_writer_queue, get_scraper_instance, handle_session_expiration
    )
    import database
    from services.auto_booking_service import process_auto_bookings_job
    from logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

scheduler = None


def graceful_shutdown(signum, frame):

    logger.info(
        f"Scheduler received signal {signum}. Shutting down gracefully...")
    if scheduler:
        scheduler.shutdown()
    logger.info("Scheduler shut down.")
    sys.exit(0)


def run_process_auto_bookings():
    """
    Wrapper function to inject dependencies into process_auto_bookings_job.
    This allows APScheduler to pickle the job reference without pickling the complex app object.
    """
    process_auto_bookings_job(
        app_instance=app,
        debug_writer_queue_instance=debug_writer_queue,
        get_scraper_instance_func=get_scraper_instance,
        handle_session_expiration_func=handle_session_expiration
    )


def run_scheduler():
    global scheduler
    logger.info("Starting standalone scheduler process...")

    jobstores: Dict[str, SQLAlchemyJobStore] = {
        'default': SQLAlchemyJobStore(url=f'sqlite:///{database.DATABASE_FILE}?timeout=15')
    }

    # Using a ThreadPoolExecutor to handle concurrent jobs.
    # This allows multiple booking jobs to run in parallel,
    # preventing one user's attempt from blocking another's.
    executors: Dict[str, ThreadPoolExecutor] = {
        'default': ThreadPoolExecutor(2)
    }

    scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors)

    # Add jobs to the scheduler using cron triggers for precise timing.

    # The main booking job, runs at the start of every minute.
    # Using second=1 to provide a small buffer.
    scheduler.add_job(
        run_process_auto_bookings, 'cron', minute='*', second=1, id='auto_booking_processor',
        replace_existing=True, max_instances=1
    )

    # The cancellation reminder job, runs every 5 minutes.
    scheduler.add_job(send_cancellation_reminders, 'cron', minute='*/5', second=1,
                      id='cancellation_reminder_sender', replace_existing=True, max_instances=1, misfire_grace_time=30)

    # The reset failed bookings job, runs once daily just after midnight.
    scheduler.add_job(reset_failed_bookings, 'cron', hour=0, minute=0,
                      second=1, id='reset_failed_bookings_job', replace_existing=True)

    # Proactively refresh all user sessions every 2 hours.
    scheduler.add_job(refresh_sessions, 'cron', hour='*/2', minute=0,
                      second=1, id='session_refresher', replace_existing=True)

    scheduler.start()
    logger.info("Scheduler started and running.")

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    try:
        # Keep the main thread alive, otherwise the script will exit.
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        # This block might not be reached if signal handler catches SIGINT,
        # but good to keep as fallback if signal handling fails or behavior varies.
        graceful_shutdown(signal.SIGINT, None)


if __name__ == '__main__':
    run_scheduler()
