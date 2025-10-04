import requests
from bs4 import BeautifulSoup
import logging
from datetime import date, timedelta
import re

# Constants
LOGIN_URL = 'https://www.workoutbristol.co.uk/login'
MEMBERS_URL = 'https://www.workoutbristol.co.uk/members'
BOOKING_URL = 'https://www.workoutbristol.co.uk/book-classes'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
BASE_HEADERS = {
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': USER_AGENT
}

class Scraper:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.csrf_token = None
        self._login()

    def _get_csrf_token(self):
        """Fetch the CSRF token from the meta tag."""
        try:
            response = self.session.get(LOGIN_URL, headers={'User-Agent': USER_AGENT})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            token = soup.find('meta', {'name': 'csrf-token'})['content']
            return token
        except (requests.exceptions.RequestException, KeyError, TypeError) as e:
            logging.error(f"Failed to retrieve CSRF token: {e}")
            return None

    def _login(self):
        """Establish a session by logging in."""
        logging.info("Attempting to establish session and log in...")
        self.csrf_token = self._get_csrf_token()
        if not self.csrf_token:
            raise Exception("Could not get CSRF token. Login failed.")

        payload = {
            'login': self.username,
            'password': self.password,
        }
        headers = {
            **BASE_HEADERS,
            'X-Winter-Request-Handler': 'onSignin',
            'x-csrf-token': self.csrf_token,
        }
        try:
            response = self.session.post(LOGIN_URL, data=payload, headers=headers)
            response.raise_for_status()
            if response.json().get("X_WINTER_REDIRECT"):
                logging.info("Login successful!")
                return True
            else:
                raise Exception(f"Login failed. Server responded with: {response.text}")
        except (requests.exceptions.RequestException, ValueError) as e:
            raise Exception(f"An error occurred during login: {e}")

    def get_classes(self, days_in_advance=7):
        """Fetch all available classes for the next N days."""
        all_classes = []
        for i in range(days_in_advance):
            target_date = date.today() + timedelta(days=i)
            target_date_str = target_date.strftime("%Y-%m-%d")
            logging.info(f"Fetching classes for date: {target_date_str}...")
            
            try:
                classes_html = self._get_classes_for_single_date(target_date_str)
                if classes_html:
                    parsed_classes = self._parse_classes_from_html(classes_html, target_date)
                    all_classes.extend(parsed_classes)
            except Exception as e:
                logging.warning(f"Could not retrieve classes for {target_date_str}: {e}")
                continue # Try the next day
        return all_classes

    def find_and_book_class(self, class_name, target_date_str, instructor_name=""):
        """Finds a specific class on a given date and books it."""
        logging.info(f"Attempting to book '{class_name}' on {target_date_str}")
        try:
            classes_html = self._get_classes_for_single_date(target_date_str)
            if not classes_html:
                return {"error": "Could not retrieve class list for the specified date."}
            
            # Reuse the parsing and booking logic
            return self._parse_and_execute_booking(classes_html, class_name, instructor_name)

        except Exception as e:
            logging.error(f"An unexpected error occurred during booking process: {e}")
            return {"error": f"An unexpected error occurred: {e}"}

    def _get_classes_for_single_date(self, target_date_str):
        """Helper method to fetch class HTML for a single date."""
        payload = {'date': target_date_str}
        headers = {
            **BASE_HEADERS,
            'X-Winter-Request-Handler': 'onDate',
            'X-Winter-Request-Partials': '@events',
            'x-csrf-token': self.csrf_token,
        }
        try:
            response = self.session.post(BOOKING_URL, data=payload, headers=headers)
            response.raise_for_status()
            json_response = response.json()

            if json_response.get("X_OCTOBER_REDIRECT"):
                logging.warning(f"Redirect received for {target_date_str}, attempting to re-login...")
                self._login() # Session might have expired, try logging in again
                headers['x-csrf-token'] = self.csrf_token # Update token in headers
                response = self.session.post(BOOKING_URL, data=payload, headers=headers)
                json_response = response.json()

            return json_response.get('@events')
        except (requests.exceptions.RequestException, ValueError) as e:
            logging.error(f"Failed to get classes for {target_date_str}: {e}")
            raise # Re-raise the exception to be caught by the calling method

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

            class_date = target_date.strftime("%d/%m/%Y")
            class_time = start_time_span.text.strip() if start_time_span else "N/A"
            
            parsed_classes.append({
                'name': title_tag.text.strip() if title_tag else "N/A",
                'description': description_div.get_text(strip=True) if description_div else "N/A",
                'instructor': instructor,
                'date': class_date,
                'time': class_time
            })
        return parsed_classes

    def _parse_and_execute_booking(self, classes_html, class_name, instructor_name):
        """Helper method that contains the core logic to find and book one class."""
        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})
        
        class_found_in_list = False
        for gym_class in gym_classes:
            title_tag = gym_class.find('h2', {'class': 'title'})
            title = title_tag.text.strip() if title_tag else ""

            # Normalize and check if the class matches
            if class_name.lower() in title.lower() and (not instructor_name or instructor_name.lower() in "instructor_placeholder"): # Placeholder
                class_found_in_list = True
                logging.info(f"Found matching class: {title}")
                
                form = gym_class.find('form', {'data-request': True})
                if not form:
                    already_booked_msg = gym_class.find(string=re.compile("you are already registered|you are on the waiting list", re.I))
                    if already_booked_msg:
                        return {"status": "info", "message": already_booked_msg.strip()}
                    return {"status": "error", "message": "Class found, but no booking form was available."}

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
                    **BASE_HEADERS,
                    'X-Winter-Request-Handler': handler,
                    'x-csrf-token': self.csrf_token,
                }
                
                logging.info(f"Attempting {action_description} for class ID {booking_payload['id']}...")
                response = self.session.post(BOOKING_URL, data=booking_payload, headers=headers)
                response.raise_for_status()
                
                if "X_OCTOBER_REDIRECT" not in response.text:
                    logging.info(f"SUCCESS! The {action_description} appears to have been successful.")
                    return {"status": "success", "action": action_description, "details": response.json()}
                else:
                    logging.warning(f"The {action_description} failed. Server responded with a redirect.")
                    return {"status": "error", "message": "Action failed. The server responded with a redirect.", "details": response.json()}

        if not class_found_in_list:
            return {"status": "error", "message": "Specified class not found on the given date."}
        
        return {"status": "error", "message": "An unknown error occurred after finding class."}

    def find_and_cancel_booking(self, class_name, target_date_str, instructor_name=""):
        """Finds a specific class on a given date and cancels the booking."""
        logging.info(f"Attempting to cancel '{class_name}' on {target_date_str}")
        try:
            classes_html = self._get_classes_for_single_date(target_date_str)
            if not classes_html:
                return {"error": "Could not retrieve class list for the specified date."}
            
            # We can reuse the booking parser, but look for the cancel action
            return self._parse_and_execute_cancellation(classes_html, class_name, instructor_name)

        except Exception as e:
            logging.error(f"An unexpected error occurred during cancellation process: {e}")
            return {"error": f"An unexpected error occurred: {e}"}

    def _parse_and_execute_cancellation(self, classes_html, class_name, instructor_name):
        """Helper method that finds a class and cancels the booking."""
        soup = BeautifulSoup(classes_html, 'html.parser')
        gym_classes = soup.find_all('div', {'class': 'class grid'})
        
        class_found_in_list = False
        for gym_class in gym_classes:
            title_tag = gym_class.find('h2', {'class': 'title'})
            title = title_tag.text.strip() if title_tag else ""

            if class_name.lower() in title.lower() and (not instructor_name or instructor_name.lower() in "instructor_placeholder"): # Placeholder
                class_found_in_list = True
                logging.info(f"Found matching class: {title}")
                
                form = gym_class.find('form', {'data-request': 'onBook'})
                if not form:
                    return {"status": "error", "message": "Class found, but no form was available."}

                # Check for a cancel button specifically
                button = form.find('button', {'class': 'cancel'})
                if not button:
                    return {"status": "error", "message": "You do not appear to be booked on this class, so cancellation is not possible."}

                handler = form.get('data-request')
                class_id_input = form.find('input', {'name': 'id'})
                timestamp_input = form.find('input', {'name': 'timestamp'})

                if not (handler and class_id_input and timestamp_input and class_id_input.get('value') and timestamp_input.get('value')):
                    return {"status": "error", "message": "Could not extract required data from the cancellation form."}

                cancellation_payload = {
                    'id': class_id_input.get('value'),
                    'timestamp': timestamp_input.get('value'),
                }
                headers = {
                    **BASE_HEADERS,
                    'X-Winter-Request-Handler': handler,
                    'x-csrf-token': self.csrf_token,
                }
                
                logging.info(f"Attempting cancellation for class ID {cancellation_payload['id']}...")
                response = self.session.post(BOOKING_URL, data=cancellation_payload, headers=headers)
                response.raise_for_status()
                
                if "X_OCTOBER_REDIRECT" not in response.text:
                    logging.info("SUCCESS! The cancellation appears to have been successful.")
                    return {"status": "success", "action": "cancellation", "details": response.json()}
                else:
                    logging.warning(f"The cancellation failed. Server responded with a redirect.")
                    return {"status": "error", "message": "Action failed. The server responded with a redirect.", "details": response.json()}

        if not class_found_in_list:
            return {"status": "error", "message": "Specified class not found on the given date."}
        
        return {"status": "error", "message": "An unknown error occurred after finding class."}

    def get_my_bookings(self):
        """Scrapes the members area to get a list of current bookings and waiting list entries."""
        logging.info("Attempting to scrape members area for bookings...")
        try:
            response = self.session.get(MEMBERS_URL, headers={'User-Agent': USER_AGENT})
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            my_bookings = []
            
            # Find the container for upcoming bookings
            bookings_container = soup.find('div', {'id': 'upcoming_bookings'})
            if not bookings_container:
                logging.warning("Could not find 'upcoming_bookings' container on members page.")
                return [] # Return an empty list if the container isn't found

            # Find all list items within the container
            booking_items = bookings_container.find_all('li')

            for item in booking_items:
                status = "Booked"
                # Check for and remove the WAITINGLIST tag to clean up the text
                waitlist_tag = item.find('strong')
                if waitlist_tag and 'WAITINGLIST' in waitlist_tag.text:
                    status = "Waiting List"
                    waitlist_tag.decompose() # Remove the tag to make parsing easier
                
                full_text = item.get_text(strip=True)
                
                # Parse the text e.g., "Vinyasa Yoga - Monday 6th October 19:45"
                match = re.search(r'(.*?)\s*-\s*(.*?)\s*(\d{2}:\d{2})', full_text)
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

            logging.info(f"Successfully parsed {len(my_bookings)} bookings.")
            return my_bookings

        except Exception as e:
            logging.error(f"An unexpected error occurred while scraping bookings: {e}")
            # In case of error, return a dict with an error key as per API design
            return {"error": f"An unexpected error occurred: {e}"}

    def get_class_availability(self, class_name, target_date_str):
        """Gets the availability for a specific class on a given date."""
        logging.info(f"Checking availability for '{class_name}' on {target_date_str}")
        try:
            classes_html = self._get_classes_for_single_date(target_date_str)
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

        except Exception as e:
            logging.error(f"An unexpected error occurred during availability check: {e}")
            return {"error": f"An unexpected error occurred: {e}"}
