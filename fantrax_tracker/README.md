# Fantrax Transaction History Tracker

A Python tool that fetches your Fantrax fantasy-sports transaction history and renders it as a visual tree chart — one row per season, showing every add, drop, and trade with colour-coded nodes and connecting lines between trade partners and between a player's acquisition and eventual departure.

## Example output

Year labels sit on the left as coloured boxes, one band per season. Player nodes spread out horizontally within each band, and lines trace both trade pairs (who-for-who) and a player's lineage across years (added in 2023, traded away in 2024, for example).

| Colour | Transaction type |
|--------|-----------------|
| 🟢 Green  | Waiver / free-agent add |
| 🔴 Red    | Drop |
| 🟡 Gold   | Trade in (received) |
| 🟠 Orange | Trade out (sent away) |

---

## Installation

```bash
pip install -r requirements.txt
```

`selenium` and `webdriver-manager` (also in requirements.txt) are only used for private-league login — skip them if your league is public.

---

## Quick start

### Public league

```bash
python main.py \
    --league-id  96igs4677sgjk7ol \
    --team       "My Team Name" \
    --output     my_history.png
```

### Private league (first run opens a headless Chrome login)

```bash
python main.py \
    --league-id  96igs4677sgjk7ol \
    --team       "My Team Name" \
    --username   you@example.com \
    --password   yourpassword \
    --output     my_history.png
```

After the first login the session cookie is saved to `fantraxloggedin.cookie` in the current directory and reused automatically on subsequent runs, so you only need `--username`/`--password` again if the cookie expires.

### Multiple seasons

Unless your league is a persistent dynasty/keeper league, Fantrax issues a **new league ID every season** — last year's history lives under a different ID than this year's. Find prior-season IDs via Fantrax's season switcher / league history link, then pass them all comma-separated:

```bash
python main.py \
    --league-id  idFor2024,idFor2025,idFor2026 \
    --team       "My Team Name" \
    --output     my_history.png
```

Each ID is fetched (and authenticated, if needed) independently and merged into one chart spanning every season, with lineage lines connecting a player's journey across the season boundary. If your login cookie/credentials work for one season they'll work for all of them, since Fantrax logins aren't scoped to a single league.

### Private league, no Selenium-drivable browser available

The `--username`/`--password` flow needs Selenium to drive a real Chrome/Chromium binary. If your only install is via Flatpak, Selenium can't launch it (the Flatpak launcher isn't a real executable ChromeDriver can start). Import a cookie from any browser instead:

```bash
python import_cookie.py
```

It prompts for the `Cookie` request header value (DevTools → Network tab → any `fantrax.com` request → Headers). See `import_cookie.py`'s docstring for the full steps. Once imported, run `main.py` with no `--username`/`--password` — it reuses the saved cookie the same way the Selenium flow does.

---

## All options

| Flag | Default | Description |
|------|---------|-------------|
| `--league-id` | *(required)* | Fantrax league ID, or comma-separated IDs (one per season) |
| `--team` | *(required)* | Your fantasy team name (partial match, case-insensitive) |
| `--username` | `""` | Fantrax login e-mail (private leagues only) |
| `--password` | `""` | Fantrax password (private leagues only) |
| `--output` | `transaction_history.png` | Output PNG path. Use `none` to skip saving |
| `--max` | `2000` | Max raw transaction rows fetched from the API |
| `--show` | off | Open the chart in a window after saving |
| `--years` | all | Comma-separated years to include, e.g. `2022,2023,2024` |
| `--no-drops` | off | Omit drop transactions from the chart |

---

## Files

```
fantrax_tracker/
├── main.py           # CLI entry point
├── auth.py           # Cookie-based auth helper (monkey-patches fantraxapi)
├── import_cookie.py  # One-time manual cookie import (Selenium alternative)
├── fetch.py          # Fetches and groups transactions by year
├── chart.py          # Builds and saves the matplotlib chart
└── requirements.txt
```

---

## Finding your league ID

The league ID is the string of characters in your Fantrax league URL:

```
https://www.fantrax.com/fantasy/league/96igs4677sgjk7ol/...
                                        ^^^^^^^^^^^^^^^^
```

---

## Notes

- The Fantrax API is unofficial and subject to change.
- Claims/drops and trades are fetched as two separate, server-side team-scoped
  queries (`--max` is a per-view cap), then merged. Each trade row already
  carries the sending and receiving team directly, so multi-team and N-for-M
  trades resolve correctly without guessing.
- Rows referencing a team no longer active in the league, or otherwise malformed,
  are skipped rather than crashing the fetch — this happens naturally with
  multi-season history in leagues with roster/ownership changes.
- A player added and dropped again within the same season, or a multi-player
  trade, can still land far apart horizontally in the chart even though the
  connecting line is correct — the layout doesn't try to minimize line length,
  only to avoid ever overlapping two nodes.
- A season with heavy transaction volume wraps onto multiple sub-rows (rather
  than one arbitrarily wide row) so the image stays within the pixel-dimension
  limits most viewers and browsers support. Lineage lines crossing a wrap
  point show as a jog instead of a straight vertical drop — expected, not a bug.
