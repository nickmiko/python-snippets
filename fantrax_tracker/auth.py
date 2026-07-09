"""Cookie-based authentication for private Fantrax leagues.

Fantrax has no documented login endpoint, so authentication works by driving
a headless Chrome browser through the real login form once, then reusing the
resulting session cookie on every subsequent request. See the fantraxapi
docs' "Connecting with a private league" section for the reference
implementation this module adapts.

Usage:
    from auth import patch_league_auth
    patch_league_auth(league, username="you@example.com", password="secret")

The first run opens a headless Chrome window to log in and saves the session
cookie to ``fantraxloggedin.cookie`` in the current directory. Subsequent
runs reuse the saved cookie automatically and only re-login if it expires.
"""

from __future__ import annotations

import os
import pickle
import time

from fantraxapi import NotLoggedIn
from fantraxapi import api as fantrax_api
from fantraxapi.api import Method
from requests import Session

COOKIE_FILE = "fantraxloggedin.cookie"


def _load_cookie(session: Session, cookie_file: str) -> None:
    with open(cookie_file, "rb") as f:
        for cookie in pickle.load(f):
            session.cookies.set(cookie["name"], cookie["value"])


def import_cookie_header(cookie_header: str, cookie_file: str = COOKIE_FILE) -> int:
    """Save a raw browser ``Cookie:`` request header as the login cookie file.

    An alternative to the Selenium login flow for environments where a
    Selenium-drivable Chrome/Chromium isn't available (e.g. Chrome installed
    only via Flatpak, whose sandboxed launcher isn't a real binary ChromeDriver
    can start).

    To get the header: log into Fantrax normally in any browser, open
    DevTools' Network tab, reload, click any request to fantrax.com, and copy
    the full value of the "Cookie" request header. Using the request header
    (rather than ``document.cookie``) matters because Fantrax's session
    cookie is typically ``httpOnly`` and so invisible to page JavaScript -
    the request header is exactly what the browser actually sends and always
    includes it.

    Returns:
        The number of cookies parsed and saved.
    """
    cookies = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip()})
    if not cookies:
        raise ValueError("No cookies found in that string - expected \"name1=value1; name2=value2\" format.")
    with open(cookie_file, "wb") as f:
        pickle.dump(cookies, f)
    return len(cookies)


def _login_and_save_cookie(session: Session, username: str, password: str, cookie_file: str) -> None:
    """Drive a headless Chrome login and persist the resulting session cookie."""
    from selenium import webdriver
    from selenium.webdriver import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    service = Service(ChromeDriverManager().install())
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1600")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36"
    )

    with webdriver.Chrome(service=service, options=options) as driver:
        driver.get("https://www.fantrax.com/login")
        email_box = WebDriverWait(driver, 10).until(
            expected_conditions.presence_of_element_located((By.XPATH, "//input[@formcontrolname='email']"))
        )
        email_box.send_keys(username)
        password_box = WebDriverWait(driver, 10).until(
            expected_conditions.presence_of_element_located((By.XPATH, "//input[@formcontrolname='password']"))
        )
        password_box.send_keys(password)
        password_box.send_keys(Keys.ENTER)
        time.sleep(5)

        cookies = driver.get_cookies()
        with open(cookie_file, "wb") as cookie_f:
            pickle.dump(cookies, cookie_f)
        for cookie in cookies:
            session.cookies.set(cookie["name"], cookie["value"])


def patch_league_auth(username: str = "", password: str = "", cookie_file: str = COOKIE_FILE) -> None:
    """Monkey-patch ``fantraxapi.api.request`` so requests auto-authenticate.

    Must be called *before* constructing any ``League`` - the constructor
    itself makes an authenticated request (``getFantasyLeagueInfo``), so
    patching afterward is too late to catch it.

    On the first unauthenticated request this loads a saved cookie if one
    exists, or logs in via Selenium and saves a fresh cookie otherwise. If a
    request still comes back ``NotLoggedIn`` (e.g. the saved cookie expired),
    it discards the stale cookie, re-logs in, and retries once.

    Args:
        username: Fantrax login e-mail, required if no valid cookie is saved.
        password: Fantrax password, required if no valid cookie is saved.
        cookie_file: Path to the pickle file used to cache the session cookie.
    """
    original_request = fantrax_api.request

    def _add_cookie(session: Session, ignore_saved: bool = False) -> None:
        if not ignore_saved and os.path.exists(cookie_file):
            _load_cookie(session, cookie_file)
        else:
            if not username or not password:
                raise RuntimeError(
                    "No saved login cookie found and no username/password provided. "
                    "Pass --username and --password to log in."
                )
            _login_and_save_cookie(session, username, password, cookie_file)

    def new_request(lg, methods: "list[Method] | Method") -> dict:
        try:
            if not lg.logged_in:
                _add_cookie(lg.session)
            return original_request(lg, methods)
        except NotLoggedIn:
            _add_cookie(lg.session, ignore_saved=True)
            return original_request(lg, methods)

    fantrax_api.request = new_request
