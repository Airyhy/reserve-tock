import threading
import time

from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.chrome.options import Options
from datetime import timedelta
import re
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.support import expected_conditions as EC


# Example: start from October 1, 2025
START_DATE = datetime(2025, 9, 21)
NUM_DAYS = 7

# Login not required for Tock. Leave it as false to decrease reservation delay
ENABLE_LOGIN = False

# Set your specific reservation month and days
RESERVATION_TIME_FORMAT = "%I:%M %p"
# VENUE_SLUG = "fui-hui-hua-san-francisco"
VENUE_SLUG = "straitsrestaurant"

TOCK_USERNAME = "SET_YOUR_USER_NAME_HERE"
TOCK_PASSWORD = "SET_YOUR_PASSWORD_HERE"

# Set the time range for acceptable reservation times.
# I.e., any available slots between 5:00 PM and 8:30 PM
EARLIEST_TIME = "5:00 PM"
LATEST_TIME = "8:30 PM"
MID_TIME = "18:00"
RESERVATION_TIME_MIN = datetime.strptime(EARLIEST_TIME, RESERVATION_TIME_FORMAT)
RESERVATION_TIME_MAX = datetime.strptime(LATEST_TIME, RESERVATION_TIME_FORMAT)

# Set the party size for the reservation
RESERVATION_SIZE = 2

# Multithreading configurations
NUM_THREADS = 1
THREAD_DELAY_SEC = 1
RESERVATION_FOUND = False

# Time between each page refresh in milliseconds. Decrease this time to
# increase the number of reservation attempts
REFRESH_DELAY_MSEC = 30000

# Chrome extension configurations that are used with Luminati.io proxy.
# Enable proxy to avoid getting IP potentially banned. This should be enabled only if the REFRESH_DELAY_MSEC
# is extremely low (sub hundred) and NUM_THREADS > 1.
ENABLE_PROXY = False
USER_DATA_DIR = "~/Library/Application Support/Google/Chrome"
PROFILE_DIR = "Default"
# https://chrome.google.com/webstore/detail/luminati/efohiadmkaogdhibjbmeppjpebenaool
EXTENSION_PATH = (
    USER_DATA_DIR
    + "/"
    + PROFILE_DIR
    + "/Extensions/efohiadmkaogdhibjbmeppjpebenaool/1.149.316_0"
)

# Delay for how long the browser remains open so that the reservation can be finalized. Tock holds the reservation
# for 10 minutes before releasing.
BROWSER_CLOSE_DELAY_SEC = 600

WEBDRIVER_TIMEOUT_SEC = 5


def generate_week_dates(start_date, num_days=7):
    return [
        (start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)
    ]


DATES_TO_CHECK = generate_week_dates(START_DATE, NUM_DAYS)


TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}\s?[AP]M)\b", re.IGNORECASE)


def extract_times(text: str):
    return [m.upper() for m in TIME_RE.findall(text)]


class ReserveTFL:
    def __init__(self):
        options = Options()
        if ENABLE_PROXY:
            options.add_argument(f"--load-extension={EXTENSION_PATH}")
            options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
            options.add_argument("--profile-directory=Default")

        # Visible Chrome (no headless) so you can watch it
        self.driver = webdriver.Chrome(options=options)

    def teardown(self):
        self.driver.quit()

    # Build a direct date URL for this venue (bypasses calendar UI)
    def build_search_url_date(
        self, date_yyyy_mm_dd: str, party_size: int, seed_time_24h: str
    ):
        # Example:
        # https://www.exploretock.com/fui-hui-hua-san-francisco/search?date=2025-10-23&size=2&time=22%3A00
        return (
            f"https://www.exploretock.com/{VENUE_SLUG}/search"
            f"?date={date_yyyy_mm_dd}&size={party_size}&time={seed_time_24h.replace(':','%3A')}"
        )

    def expand_all_times(self):
        while True:
            try:
                more = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "//a[contains(.,'more times')] | //button[contains(.,'more times')]",
                        )
                    )
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", more
                )
                more.click()
                time.sleep(0.2)
            except TimeoutException:
                break

    def reserve(self):
        global RESERVATION_FOUND
        dates_to_check = generate_week_dates(START_DATE, NUM_DAYS)

        while not RESERVATION_FOUND:
            for date_str in dates_to_check:
                url = self.build_search_url_date(date_str, RESERVATION_SIZE, MID_TIME)
                print("OPENING:", url)
                self.driver.get(url)

                try:
                    WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_SEC).until(
                        expected_conditions.presence_of_all_elements_located(
                            (By.XPATH, "//div[@data-testid='search-result']")
                        )
                    )
                except TimeoutException:
                    continue

                if self.search_time():
                    print(f"Found slot on {date_str}. Holding browser for 10 minutes…")
                    RESERVATION_FOUND = True
                    time.sleep(BROWSER_CLOSE_DELAY_SEC)
                    return

            time.sleep(REFRESH_DELAY_MSEC / 100.0)

    def login_tock(self):
        self.driver.get(f"https://www.exploretock.com/{VENUE_SLUG}/login")
        WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_SEC).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        self.driver.find_element(By.NAME, "email").send_keys(TOCK_USERNAME)
        self.driver.find_element(By.NAME, "password").send_keys(TOCK_PASSWORD)
        self.driver.find_element(By.CSS_SELECTOR, ".Button").click()
        WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_SEC).until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, ".MainHeader-accountName")
            )
        )

    def search_time(self):
        # each slot row/card
        cards = self.driver.find_elements(
            By.XPATH, "//div[@data-testid='search-result']"
        )

        for i in range(len(cards)):
            try:
                card = self.driver.find_elements(
                    By.XPATH, "//div[@data-testid='search-result']"
                )[i]
            except (IndexError, StaleElementReferenceException):
                continue

            card_text = (card.text or "").strip()
            if not card_text:
                continue
            # e.g., "11:30 AM ... Book"
            times = extract_times(card_text)
            # print("CARD:", repr(card_text), "TIMES:", times)  # debug if needed

            chosen = None
            for ts in times:
                try:
                    t = datetime.strptime(ts, RESERVATION_TIME_FORMAT)  # "%I:%M %p"
                    if RESERVATION_TIME_MIN <= t <= RESERVATION_TIME_MAX:
                        chosen = ts
                        break
                except Exception:
                    continue
            if not chosen:
                continue

            # find the Book button inside this card
            book = None
            # primary: data-testid is very reliable on Tock
            try:
                book = card.find_element(
                    By.XPATH, ".//button[@data-testid='booking-card-button']"
                )
            except Exception:
                # fallbacks (some skins wrap the label in a <span>)
                try:
                    book = card.find_element(
                        By.XPATH,
                        ".//button[normalize-space()='Book' or .//span[normalize-space()='Book']]",
                    )
                except Exception:
                    continue  # nothing clickable here

            # scroll into view & click
            try:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", book
                )
            except Exception:
                pass
            try:
                WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_SEC).until(
                    EC.element_to_be_clickable(book)
                )
                book.click()
            except (ElementClickInterceptedException, TimeoutException):
                self.driver.execute_script("arguments[0].click();", book)

            # confirm we moved to hold/checkout
            try:
                WebDriverWait(self.driver, WEBDRIVER_TIMEOUT_SEC * 2).until(
                    EC.any_of(
                        EC.url_contains("reservation"),
                        EC.url_contains("checkout"),
                        EC.presence_of_element_located(
                            (
                                By.XPATH,
                                "//button[contains(.,'Continue') or contains(.,'Next') or contains(.,'Checkout')]",
                            )
                        ),
                    )
                )
            except TimeoutException:
                pass

            print(f"Clicked Book for {chosen}")
            return True

        return False


def run_reservation():
    r = ReserveTFL()
    r.reserve()
    r.teardown()


def execute_reservations():
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(target=run_reservation)
        threads.append(t)
        t.start()
        time.sleep(THREAD_DELAY_SEC)

    for t in threads:
        t.join()


# def continuous_reservations():
#     while True:
#         execute_reservations()


# continuous_reservations()
run_reservation()
