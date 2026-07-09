"""One-time helper: import a Fantrax login cookie copied from a browser.

Use this instead of --username/--password when Selenium can't drive your
installed Chrome/Chromium (e.g. it's only available via Flatpak).

How to get the cookie value:
  1. Log into fantrax.com normally in any browser.
  2. Open DevTools -> Network tab, then reload the page.
  3. Click any request made to fantrax.com.
  4. In the Headers panel, find the request header named "Cookie" and copy
     its full value (looks like "JSESSIONID=abc123; XSRF-TOKEN=xyz; ...").

Usage:
    python import_cookie.py "JSESSIONID=abc123; XSRF-TOKEN=xyz; ..."

Or run with no argument to be prompted (input isn't echoed to the terminal,
so it won't land in shell history):
    python import_cookie.py
"""

from __future__ import annotations

import sys

from auth import COOKIE_FILE, import_cookie_header


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        cookie_header = argv[0]
    else:
        import getpass

        cookie_header = getpass.getpass("Paste the Cookie header value: ")

    count = import_cookie_header(cookie_header)
    print(f"Saved {count} cookies to {COOKIE_FILE}")
    print("You can now run main.py without --username/--password.")


if __name__ == "__main__":
    main()
