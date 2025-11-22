import requests
import config
from bs4 import BeautifulSoup
import logging
from datetime import date, timedelta, datetime
import re
from thefuzz import fuzz
import random
import time

# --- Custom Exceptions ---
class SessionExpiredError(Exception):
    """Custom exception to indicate that the user's session has expired."""
    pass

# --- Constants ---
BASE_URL = config.WEBSITE_URL
LOGIN_URL = BASE_URL + 'login'
MEMBERS_URL = BASE_URL + 'members'
BOOKING_URL = BASE_URL + 'book-classes'

USER_AGENTS = [
    # Chrome on Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    
    # Chrome on macOS
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',

    # Firefox on Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',

    # Firefox on macOS
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0',

    # Safari on macOS
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15',

    # Edge on Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.67',
]

from functools import wraps

def handle_session_expiry(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except SessionExpiredError as e:
            logging.warning(f"Session expired during {func.__name__}. Attempting re-login.")
            if self._login():
                logging.info("Re-login successful. Retrying original operation.")
                return func(self, *args, **kwargs)
            else:
                raise SessionExpiredError(f"Automatic re-login failed during {func.__name__}.") from e
    return wrapper

class Scraper:
    def __init__(self, username, password, session_data=None):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.csrf_token = None
        self.relogin_failures = 0
        self.disabled_until = None
        self.user_agent = random.choice(USER_AGENTS)
        self.base_headers = {
            'User-Agent': self.user_agent,
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': BASE_URL.rstrip('/'),
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
        }

        if session_data:
            self.from_dict(session_data)
        else:
            if not self._login():
                raise Exception("Initial login failed.")

    def to_dict(self):
        """Serializes the session cookies and CSRF token to a dictionary."""
        return {
            'cookies': self.session.cookies.get_dict(),
            'csrf_token': self.csrf_token
        }

    def from_dict(self, data):
        """Deserializes the session from a dictionary."""
        self.session.cookies.update(data.get('cookies', {}))
        self.csrf_token = data.get('csrf_token')

    def _get_csrf_token(self):
        """Fetch the CSRF token from the meta tag."""
        try:
            headers = {
                'User-Agent': self.user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
            }
            response = self.session.get(LOGIN_URL, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            token = soup.find('meta', {'name': 'csrf-token'})['content']
            return token
        except (requests.exceptions.RequestException, KeyError, TypeError) as e:
            logging.error(f"Failed to retrieve CSRF token: {e}")
            return None

    def _login(self):
        """Establish a session by logging in. Returns True on success, False on failure."""
        if self.disabled_until and datetime.now() < self.disabled_until:
            logging.warning(f"Scraper for {self.username} is temporarily disabled due to repeated login failures.")
            return False

        logging.info(f"Attempting to establish session and log in for {self.username}...")
        try:
            self.csrf_token = self._get_csrf_token()
            if not self.csrf_token:
                raise Exception("Could not get CSRF token.")

            payload = {
                'login': self.username,
                'password': self.password,
            }
            headers = {
                **self.base_headers,
                'Referer': LOGIN_URL,
                'X-Winter-Request-Handler': 'onSignin',
                'x-csrf-token': self.csrf_token,
            }
            response = self.session.post(LOGIN_URL, data=payload, headers=headers)
            response.raise_for_status()

            if response.json().get("X_WINTER_REDIRECT"):
                logging.info(f"Login successful for {self.username}!")
                self.relogin_failures = 0
                self.disabled_until = None
                return True
            else:
                raise Exception(f"Login failed. Server responded with: {response.text}")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 500 and e.request.url == LOGIN_URL:
                logging.warning(f"Login failed for {self.username} with a 500 error, which may indicate an incorrect password.")
            else:
                logging.error(f"HTTP Error during login for {self.username}: {e.response.status_code} - {e.response.reason} for url: {e.request.url}")
            
            self.relogin_failures += 1
            if self.relogin_failures >= 3:
                self.disabled_until = datetime.now() + timedelta(minutes=15)
                logging.critical(f"Disabling scraper for {self.username} for 15 minutes due to {self.relogin_failures} consecutive login failures.")
            return False

    @handle_session_expiry
    def get_classes(self, days_in_advance=7):
        """Fetch all available classes for the next N days."""
        all_classes = []
        for i in range(days_in_advance):
            target_date = date.today() + timedelta(days=i)
            target_date_str = target_date.strftime("%Y-%m-%d")
            logging.info(f"Fetching classes for date: {target_date_str}...")
            
            classes_json_response = self._get_classes_for_single_date(target_date_str)
            classes_html = classes_json_response.get('@events')
            if classes_html:
                parsed_classes = self._parse_classes_from_html(classes_html, target_date)
                all_classes.extend(parsed_classes)
        return all_classes

    @handle_session_expiry
    def find_and_book_class(self, target_date_str, class_name="", target_time="", instructor=""):
        """Finds and books a class. Can match by class name or by time/instructor."""
        if target_time and class_name:
            logging.info(f"Attempting to book class like '{class_name}' at {target_time} on {target_date_str}")
        else:
            logging.info(f"Attempting to book '{class_name}' on {target_date_str}")
        
        json_response = self._get_classes_for_single_date(target_date_str)
        classes_html = json_response.get('@events')

        if not classes_html:
            return {"status": "error", "message": "Could not retrieve class list HTML for the specified date.", "html_content": json.dumps(json_response, indent=2)}
        
        return self._parse_and_execute_booking(classes_html, class_name, target_time, instructor, target_date_str)

    @handle_session_expiry
    def _get_classes_for_single_date(self, target_date_str):
        """Helper method to fetch class HTML for a single date."""
        time.sleep(random.uniform(1, 2))
        if self.disabled_until and datetime.now() < self.disabled_until:
            raise Exception(f"Scraper for {self.username} is temporarily disabled.")

        self.csrf_token = self._get_csrf_token()
        if not self.csrf_token:
            raise SessionExpiredError("Failed to get a fresh CSRF token before first attempt.")

        payload = {'date': target_date_str}
        headers = {
            **self.base_headers,
            'X-Winter-Request-Handler': 'onDate',
            'X-Winter-Request-Partials': '@events',
            'x-csrf-token': self.csrf_token,
        }
        
        response = self.session.post(BOOKING_URL, data=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()

        if json_response.get("X_OCTOBER_REDIRECT"):
            raise SessionExpiredError("Redirect received, indicating session has expired.")

        return json_response

    def _parse_classes_from_html(self, classes_html, target_date):
        """Helper method to parse class details from HTML."""
        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})
        parsed_classes = []
        for gym_class in gym_classes:
            title_tag = gym_class.find('h2', {'class': 'title'})
            description_div = gym_class.find('div', {'class': 'description'})
            start_time_span = gym_class.find('span', {'itemprop': 'startDate'})
            
            instructor = ""
            p_tags = gym_class.find_all('p')
            for p in p_tags:
                if p.text.lower().strip().startswith('with '):
                    instructor = p.text.strip()[5:].replace('.','')
                    break

            start_time_span = gym_class.find('span', {'itemprop': 'startDate'})
            end_time_span = gym_class.find('span', {'itemprop': 'endDate'})

            start_time_str = start_time_span.text.strip() if start_time_span else "N/A"
            end_time_str = end_time_span.text.strip() if end_time_span else "N/A"

            duration = "N/A"
            if start_time_str != "N/A" and end_time_str != "N/A":
                try:
                    start_dt = datetime.strptime(start_time_str, '%H:%M')
                    end_dt = datetime.strptime(end_time_str, '%H:%M')
                    # If end_dt is before start_dt, it means it's the next day
                    if end_dt < start_dt:
                        end_dt += timedelta(days=1)
                    duration_td = end_dt - start_dt
                    duration = int(duration_td.total_seconds() / 60)
                except ValueError:
                    logging.warning(f"Could not parse time for duration calculation: {start_time_str} - {end_time_str}")

            class_date = target_date.strftime("%d/%m/%Y")
            
            remaining_spaces_tag = gym_class.find('span', {'class': 'remaining'})
            available_spaces = int(remaining_spaces_tag.text.strip()) if remaining_spaces_tag and remaining_spaces_tag.text.strip().isdigit() else 0

            parsed_classes.append({
                'name': title_tag.text.strip() if title_tag else "N/A",
                'description': description_div.get_text(strip=True) if description_div else "N/A",
                'instructor': instructor,
                'date': class_date,
                'start_time': start_time_str,
                'end_time': end_time_str,
                'duration': duration,
                'available_spaces': available_spaces
            })
        return parsed_classes

    @handle_session_expiry
    def _parse_and_execute_booking(self, classes_html, class_name, target_time, target_instructor, target_date_str, is_retry=False):
        """Helper method that finds and books a class."""
        self.csrf_token = self._get_csrf_token() # Refresh CSRF token
        if not self.csrf_token:
            raise Exception("Could not get a fresh CSRF token for booking.")

        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})
        
        best_match_element = None
        highest_score = 0

        # First pass: find the best match
        for gym_class in gym_classes:
            start_time_span = gym_class.find('span', {'itemprop': 'startDate'})
            start_time_str = start_time_span.text.strip() if start_time_span else ""
            
            title_tag = gym_class.find('h2', {'class': 'title'})
            title = title_tag.text.strip() if title_tag else ""

            instructor_from_html = ""
            p_tags = gym_class.find_all('p')
            for p in p_tags:
                if p.text.lower().strip().startswith('with '):
                    instructor_from_html = p.text.strip()[5:].replace('.', '')
                    break

            # Match by time, then fuzzy match name and instructor
            if start_time_str == target_time:
                name_score = fuzz.ratio(class_name.lower(), title.lower())
                
                if target_instructor:
                    instructor_score = fuzz.ratio(target_instructor.lower(), instructor_from_html.lower())
                    score = (name_score * 0.7) + (instructor_score * 0.3)
                else:
                    score = name_score

                if score > highest_score:
                    highest_score = score
                    best_match_element = gym_class

        # Second pass: execute booking for the best match found
        # Extract title here so it's available in the else block
        found_class_title = "N/A"
        if best_match_element:
            title_tag = best_match_element.find('h2', {'class': 'title'})
            found_class_title = title_tag.text.strip() if title_tag else "Unknown"

        if best_match_element and highest_score > 46:
            title = found_class_title # Use the extracted title
            logging.info(f"Found best match for '{class_name}': '{title}' with score {highest_score}")

            already_booked_msg = best_match_element.find(string=re.compile("you are already registered|you are on the waiting list", re.I))
            if already_booked_msg:
                return {"status": "info", "message": already_booked_msg.strip()}

            form = best_match_element.find('form', {'data-request': True})
            if not form:
                return {"status": "error", "message": "Class matched, but no booking form was available (it may be full)."}

            handler = form.get('data-request')
            button = form.find('button', {'type': 'submit'})
            action_description = ""

            if button and 'signup' in button.get('class', []):
                action_description = "booking"
            elif button and 'waitinglist' in button.get('class', []):
                action_description = "waitlisting"
            else:
                return {"status": "error", "message": "Could not determine action (Book/Waitlist)."}

            class_id_input = form.find('input', {'name': 'id'})
            timestamp_input = form.find('input', {'name': 'timestamp'})

            if not (class_id_input and timestamp_input and class_id_input.get('value') and timestamp_input.get('value')):
                return {"status": "error", "message": "Could not extract required data from the booking form."}

            booking_payload = {
                'id': class_id_input.get('value'),
                'timestamp': timestamp_input.get('value'),
            }
            headers = {
                **self.base_headers,
                'X-Winter-Request-Handler': handler,
                'x-csrf-token': self.csrf_token,
            }
            
            logging.info(f"Attempting {action_description} for class ID {booking_payload['id']}...")
            response = self.session.post(BOOKING_URL, data=booking_payload, headers=headers)
            response.raise_for_status()
            
            if "X_OCTOBER_REDIRECT" in response.text:
                raise SessionExpiredError(f"The {action_description} failed, possibly due to a stale session.")

            logging.info(f"SUCCESS! The {action_description} appears to have been successful.")
            return {"status": "success", "action": action_description, "details": response.json(), "class_name": found_class_title}
        else:
            if target_time and class_name:
                 return {"status": "error", "message": f"Could not find a suitable match for '{class_name}' at {target_time}. Best match score was {highest_score}. Closest match found: '{found_class_title}'.", "html_content": classes_html}
            else:
                 return {"status": "error", "message": f"Specified class '{class_name}' not found."}

    @handle_session_expiry
    def find_and_cancel_booking(self, class_name, target_date_str, target_time, instructor_name=""):
        """Finds a specific class on a given date and cancels the booking."""
        logging.info(f"Attempting to cancel '{class_name}' at {target_time} on {target_date_str}")
        json_response = self._get_classes_for_single_date(target_date_str)
        classes_html = json_response.get('@events')
        if not classes_html:
            return {"error": "Could not retrieve class list for the specified date."}
        
        return self._parse_and_execute_cancellation(classes_html, class_name, target_time, instructor_name, target_date_str)

    @handle_session_expiry
    def _parse_and_execute_cancellation(self, classes_html, class_name, target_time, instructor_name, target_date_str, is_retry=False):
        """Helper method that finds a class and triggers the cancellation, with auto re-login."""
        self.csrf_token = self._get_csrf_token() # Refresh CSRF token
        if not self.csrf_token:
            raise Exception("Could not get a fresh CSRF token for cancellation.")

        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})
        
        target_class_element = None
        
        for gym_class in gym_classes:
            title_tag = gym_class.find('h2', {'class': 'title'})
            title = title_tag.text.strip() if title_tag else ""

            start_time_span = gym_class.find('span', {'itemprop': 'startDate'})
            start_time_str = start_time_span.text.strip() if start_time_span else ""

            # Basic match: class name and time must match
            if class_name.lower() in title.lower() and start_time_str == target_time:
                
                # If instructor is specified, it must also match
                if instructor_name:
                    instructor_from_html = ""
                    p_tags = gym_class.find_all('p')
                    for p in p_tags:
                        if p.text.lower().strip().startswith('with '):
                            instructor_from_html = p.text.strip()[5:].replace('.', '')
                            break
                    
                    if instructor_name.lower() in instructor_from_html.lower():
                        logging.info(f"Found matching class with instructor: {title}")
                        target_class_element = gym_class
                        break
                else:
                    # No instructor specified, so this is our match
                    logging.info(f"Found matching class: {title}")
                    target_class_element = gym_class
                    break

        if not target_class_element:
            return {"status": "error", "message": "Specified class not found on the given date."}

        # --- Merged logic from _perform_cancellation_on_class ---
        form = target_class_element.find('form', {'data-request': 'onBook'})
        if not form:
            return {"status": "error", "message": "Class found, but no form was available."}

        button = target_class_element.find('button', {'class': 'cancel'})
        if not button:
            return {"status": "error", "message": "You do not appear to be booked on this class, so cancellation is not possible."}

        handler = form.get('data-request')
        class_id_input = form.find('input', {'name': 'id'})
        timestamp_input = target_class_element.find('input', {'name': 'timestamp'})

        if not (handler and class_id_input and timestamp_input and class_id_input.get('value') and timestamp_input.get('value')):
            return {"status": "error", "message": "Could not extract required data from the cancellation form."}

        cancellation_payload = {
            'id': class_id_input.get('value'),
            'timestamp': timestamp_input.get('value'),
        }
        headers = {
            **self.base_headers,
            'X-Winter-Request-Handler': handler,
            'x-csrf-token': self.csrf_token,
        }
        
        logging.info(f"Attempting cancellation for class ID {cancellation_payload['id']}...")
        response = self.session.post(BOOKING_URL, data=cancellation_payload, headers=headers)
        response.raise_for_status()

        if "X_OCTOBER_REDIRECT" in response.text:
            raise SessionExpiredError("Cancellation failed, possibly due to a stale session.")
        
        logging.info("SUCCESS! The cancellation appears to have been successful.")
        return {"status": "success", "action": "cancellation", "details": response.json()}

    @handle_session_expiry
    def get_my_bookings(self):
        """Scrapes the members area to get a list of current bookings and waiting list entries."""
        logging.debug("Attempting to scrape members area for bookings...")
        response = self.session.get(MEMBERS_URL, headers={'User-Agent': self.user_agent})
        response.raise_for_status()

        # Check if we were redirected to the login page, indicating an expired session
        if LOGIN_URL in response.url:
            raise SessionExpiredError("Session expired, redirect to login page detected.")

        soup = BeautifulSoup(response.text, 'html.parser')
        my_bookings = []
        
        bookings_container = soup.find('div', {'id': 'upcoming_bookings'})
        if not bookings_container:
            logging.info("Could not find 'upcoming_bookings' container on members page. It might be empty.")
            return []

        booking_items = bookings_container.find_all('li')

        for item in booking_items:
            status = "Booked"
            waitlist_tag = item.find('strong')
            if waitlist_tag and 'WAITINGLIST' in waitlist_tag.text:
                status = "Waiting List"
                waitlist_tag.decompose()
            
            full_text = item.get_text(strip=True)
            
            match = re.search(r'(.*)\s*-\s*(.*?)\s*(\d{2}:\d{2})', full_text)
            if match:
                class_name = match.group(1).strip()
                class_date = match.group(2).strip()
                class_time = match.group(3).strip()
                
                my_bookings.append({
                    'name': class_name,
                    'date': class_date,
                    'time': class_time,
                    'status': status
                })
            else:
                logging.warning(f"Could not parse booking string: {full_text}")

        logging.debug(f"Successfully parsed {len(my_bookings)} bookings.")
        return my_bookings

    @handle_session_expiry
    def get_class_availability(self, class_name, target_date_str):
        """Gets the availability for a specific class on a given date."""
        logging.info(f"Checking availability for '{class_name}' on {target_date_str}")
        json_response = self._get_classes_for_single_date(target_date_str)
        classes_html = json_response.get('@events')
        if not classes_html:
            return {"error": "Could not retrieve class list for the specified date."}

        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})

        for gym_class in gym_classes:
            title_tag = gym_class.find('h2', {'class': 'title'})
            title = title_tag.text.strip() if title_tag else ""

            if class_name.lower() in title.lower():
                logging.info(f"Found matching class: {title}")
                remaining_spaces_tag = gym_class.find('span', {'class': 'remaining'})
                
                if remaining_spaces_tag and remaining_spaces_tag.text.isdigit():
                    spaces = int(remaining_spaces_tag.text)
                    return {
                        "class_name": title,
                        "date": target_date_str,
                        "remaining_spaces": spaces
                    }
                else:
                    return {"error": f"Could not parse remaining spaces for {title}."}
        
        return {"error": f"Class '{class_name}' not found on {target_date_str}."}
