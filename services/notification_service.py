import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pywebpush import webpush, WebPushException

try:
    from .. import config
    from .. import database
except ImportError:
    import config
    import database

logger = logging.getLogger(__name__)

def send_push_notification(username: str, title: str, body: str,
                           tag: str = "general", url: str = "/",
                           subscriptions: Optional[List[Dict[str, Any]]] = None) -> None:
    """
    Sends a WebPush notification to all active subscriptions of a user.
    If 'subscriptions' is provided, it skips the DB lookup.
    """
    if not config.VAPID_PRIVATE_KEY or not config.VAPID_PUBLIC_KEY or not config.VAPID_ADMIN_EMAIL:
        logger.warning(
            "VAPID keys or admin email not configured. Push notification aborted.")
        return

    if subscriptions is None:
        subscriptions = database.get_push_subscriptions_for_user(username)
        
    if not subscriptions:
        logger.info(f"No push subscriptions found for user: {username}")
        return

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({
                    "title": title,
                    "body": body,
                    "icon": "/favicon.png",
                    "badge": "/favicon.png",
                    "url": url,
                    "tag": tag
                }),
                vapid_private_key=config.VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": f"mailto:{config.VAPID_ADMIN_EMAIL}"
                }
            )
            logger.info(
                f"Push notification sent successfully to endpoint: {sub['endpoint']}")
        except WebPushException as ex:
            if ex.response and ex.response.status_code == 410:
                logger.info(
                    f"Subscription expired (410 Gone). Deleting endpoint: {sub['endpoint']}")
                database.delete_push_subscription(sub['endpoint'])
            else:
                logger.error(
                    f"WebPushException sending notification to {sub['endpoint']}: {repr(ex)}")
        except Exception as e:
            logger.error(f"Error sending push notification to {sub['endpoint']}: {e}")

def process_cancellation_reminders() -> None:
    """
    Checks upcoming live bookings and sends push notifications
    if they are within 3.5 hours of starting.
    """
    live_bookings_to_remind = database.get_live_bookings_for_reminder()
    if not live_bookings_to_remind:
        return

    now = datetime.now()
    
    # Pre-fetch all push subscriptions to avoid N+1 queries in the loop
    all_subs_raw = database.get_all_push_subscriptions()
    subs_by_user = {}
    
    # We need the full subscription info (keys) to send notifications.
    # get_all_push_subscriptions doesn't return the keys. We must fetch them.
    # Since get_push_subscriptions_for_user returns keys, we can optimize by only 
    # making one query per unique user instead of one per booking.
    unique_users = {booking[1] for booking in live_bookings_to_remind}
    for user in unique_users:
        subs_by_user[user] = database.get_push_subscriptions_for_user(user)

    for booking in live_bookings_to_remind:
        booking_id, username, class_name, class_date, class_time, instructor = booking

        try:
            # Reconstruct class datetime
            class_datetime_str = f"{class_date} {class_time}"
            class_datetime = datetime.strptime(class_datetime_str, '%Y-%m-%d %H:%M')
            time_until_class = class_datetime - now

            # Send notification if within the 3.5 hour window and class hasn't started yet
            if timedelta(0) < time_until_class <= timedelta(hours=3, minutes=30):
                # Mark as sent immediately to prevent duplicate sends on next cycle
                database.update_live_booking_reminder_status(booking_id, reminder_sent=1)
                
                title = "GABS Reminder ⏰"
                body = f"Your class '{class_name}' starts at {class_time}. Remember to cancel if you cannot attend to avoid a strike!"
                url = "/live-booking"
                
                logger.info(f"Sending cancellation reminder for booking {booking_id} to user {username}")
                # Pass pre-fetched subscriptions to prevent DB hits in the loop
                user_subs = subs_by_user.get(username, [])
                send_push_notification(username, title, body, tag=f"reminder-{booking_id}", url=url, subscriptions=user_subs)
                
        except Exception as e:
            logger.error(f"Error processing cancellation reminder for booking {booking_id}: {e}")
