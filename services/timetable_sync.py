import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from thefuzz import fuzz

from gabs_api_server import database, scraper, crypto
from gabs_api_server.task_logger import set_task_context, clear_task_context

STATIC_TIMETABLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static_timetable.json'
)

def _get_active_scraper() -> Optional[scraper.Scraper]:
    """Retrieves an active scraper instance using the first available valid user."""
    users = database.get_all_users()
    for username in users:
        try:
            encrypted_password, session_data = database.load_session(username)
            if encrypted_password:
                password = crypto.decrypt(encrypted_password)
                user_scraper = scraper.Scraper(username, password, session_data=session_data)
                return user_scraper
        except Exception as e:
            logging.error(f"Failed to get scraper for user {username} during timetable update: {e}")
            continue
    return None

def update_static_timetable_job() -> None:
    """Fetches the classes for the next 14 days and compiles a fresh weekly timetable."""
    try:
        set_task_context('update_static_timetable')
        logging.info("Running update_static_timetable_job...")
        
        user_scraper = _get_active_scraper()
        if not user_scraper:
            logging.error("Could not find any active user session to scrape timetable.")
            return

        # Fetch classes from the main events API list which returns the whole week's schedule
        url = "https://www.workoutbristol.co.uk/api/events/list"
        resp = user_scraper.session.get(url, headers={'User-Agent': user_scraper.user_agent}, timeout=10)
        resp.raise_for_status()
        
        all_classes = resp.json()
        
        # Load existing static timetable to preserve instructor data if possible, since api doesn't provide it
        existing_timetable = {}
        if os.path.exists(STATIC_TIMETABLE_PATH):
            try:
                with open(STATIC_TIMETABLE_PATH, 'r') as f:
                    existing_timetable = json.load(f)
            except Exception:
                pass
                
        # Mapping API event_day to day name
        days_map = {
            1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
            5: "Friday", 6: "Saturday", 7: "Sunday"
        }
        
        timetable = defaultdict(lambda: {})
        
        for cls in all_classes:
            day_name = days_map.get(cls.get('event_day'))
            if not day_name:
                continue
                
            # Filter for Ashton gym only
            if "ASHTON" not in cls.get('location_name', '').upper():
                continue
                
            start_time_raw = cls['event_time']  # e.g., "06:30:00"
            if len(start_time_raw) >= 5:
                start_time = start_time_raw[:5]  # "06:30"
            else:
                start_time = start_time_raw
                
            duration_mins = cls.get('duration', 0)
            
            try:
                start_dt = datetime.strptime(start_time, "%H:%M")
                end_dt = start_dt + timedelta(minutes=duration_mins)
                end_time = end_dt.strftime("%H:%M")
            except Exception:
                end_time = ""
                
            name = cls['title']
            
            # 1. Skip virtual classes entirely so they don't show up in UI or sync
            if "virtual" in name.lower():
                continue
            
            # Try to recover instructor from existing timetable
            instructor = ""
            if existing_timetable and day_name in existing_timetable:
                for existing_class in existing_timetable[day_name]:
                    if existing_class['name'].lower() == name.lower() and existing_class['start_time'] == start_time:
                        instructor = existing_class.get('instructor', '')
                        break
                        
            key = (start_time, name.lower())
            
            if key not in timetable[day_name]:
                timetable[day_name][key] = {
                    "name": name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "instructor": instructor
                }
                
        final_timetable = {}
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            day_classes = list(timetable[day].values())
            day_classes.sort(key=lambda x: x['start_time'])
            final_timetable[day] = day_classes
            
        with open(STATIC_TIMETABLE_PATH, 'w') as f:
            json.dump(final_timetable, f, indent=2)
            
        logging.info(f"Successfully updated static timetable at {STATIC_TIMETABLE_PATH}")

    except Exception as e:
        logging.error(f"Error in update_static_timetable_job: {e}")
    finally:
        clear_task_context()

def sync_auto_bookings_job() -> None:
    """Verifies existing pending auto-bookings against the static_timetable and updates them if needed."""
    try:
        set_task_context('sync_auto_bookings')
        logging.info("Running sync_auto_bookings_job...")
        
        if not os.path.exists(STATIC_TIMETABLE_PATH):
            logging.warning("Static timetable does not exist, skipping auto-booking sync.")
            return
            
        with open(STATIC_TIMETABLE_PATH, 'r') as f:
            timetable = json.load(f)
            
        # 1. We load the DB manually or use existing functions
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # Get all auto bookings (even 'failed' ones might need updating to be retried next week)
        cursor.execute(
            "SELECT id, username, class_name, target_time, status, day_of_week, instructor FROM auto_bookings"
        )
        all_auto_bookings = cursor.fetchall()
        
        for booking in all_auto_bookings:
            b_id, b_user, b_name, b_time, b_status, b_day, b_instructor = booking
            
            day_classes = timetable.get(b_day, [])
            if not day_classes:
                logging.warning(f"Auto-booking ID {b_id} for day {b_day} has no classes in the timetable. Marking as invalid.")
                # Optional: Handle this case (e.g. mark status as INVALID)
                continue
                
            # Find the best match in the day's classes
            best_match = None
            highest_score = 0
            
            # The user noted that time could shift by up to 10 minutes.
            b_time_obj = datetime.strptime(b_time, "%H:%M")
            
            for cls in day_classes:
                c_time_obj = datetime.strptime(cls['start_time'], "%H:%M")
                
                # Check time difference in minutes
                time_diff = abs((c_time_obj - b_time_obj).total_seconds() / 60)
                
                if time_diff <= 15: # allowing up to 15 mins
                    name_score = fuzz.ratio(b_name.lower(), cls['name'].lower())
                    
                    instructor_score = 0
                    if b_instructor and cls['instructor']:
                        instructor_score = fuzz.ratio(b_instructor.lower(), cls['instructor'].lower())
                    elif not b_instructor and not cls['instructor']:
                        instructor_score = 100
                        
                    # Overall score heavily weighted on name, lightly on instructor
                    score = (name_score * 0.8) + (instructor_score * 0.2)
                    
                    if score > highest_score:
                        highest_score = score
                        best_match = cls
                        
            # If we found a good enough match (> 60 score)
            if best_match and highest_score > 60:
                is_changed = False
                updates = []
                params = []
                
                if best_match['name'] != b_name:
                    updates.append("class_name = ?")
                    params.append(best_match['name'])
                    is_changed = True
                
                if best_match['start_time'] != b_time:
                    updates.append("target_time = ?")
                    params.append(best_match['start_time'])
                    is_changed = True
                    
                if best_match['instructor'] != b_instructor:
                    updates.append("instructor = ?")
                    params.append(best_match['instructor'])
                    is_changed = True
                    
                if is_changed:
                    query = f"UPDATE auto_bookings SET {', '.join(updates)} WHERE id = ?"
                    params.append(b_id)
                    cursor.execute(query, tuple(params))
                    logging.info(f"Updated Auto-booking ID {b_id} from ('{b_name}', '{b_time}', '{b_instructor}') to ('{best_match['name']}', '{best_match['start_time']}', '{best_match['instructor']}')")
            else:
                logging.warning(f"Auto-booking ID {b_id} for '{b_name}' at {b_time} on {b_day} no longer has a matching class in the timetable.")
                # We could set status to 'failed' or 'invalid' here if configured to do so.
                
        conn.commit()
        conn.close()
        logging.info("Finished sync_auto_bookings_job.")

    except Exception as e:
        logging.error(f"Error in sync_auto_bookings_job: {e}")
    finally:
        clear_task_context()
