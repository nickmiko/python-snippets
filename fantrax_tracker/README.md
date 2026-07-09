# Fantrax Transaction History Tracker

A Python tool that fetches your Fantrax fantasy-sports transaction history and renders it as a visual tree chart — one row per season, showing every add, drop, and trade with colour-coded nodes and connecting lines between trade partners.

## Example output

The chart produced mirrors the reference image: year labels on the left, player boxes spreading horizontally, and curved lines joining players that were part of the same trade.

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

For **private leagues** or league-restricted endpoints you also need:

```bash
pip install selenium webdriver-manager
```

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

After the first login the session cookie is saved to `fantraxloggedin.cookie`
in the current directory and reused automatically on subsequent runs.

---

## All options

| Flag | Default | Description |
|------|---------|-------------|
| `--league-id` | *(required)* | Fantrax league ID (from the URL) |
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
├── main.py          # CLI entry point
├── auth.py          # Cookie-based auth helper (monkey-patches fantraxapi)
├── fetch.py         # Fetches and groups transactions by year
├── chart.py         # Builds and saves the matplotlib chart
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
- `league.transactions(count=N)` fetches the *N* most recent transaction rows.
  If your league has many years of history, increase `--max` (e.g. `--max 5000`).
- Trade direction (in vs out) is inferred from which team owns the transaction
  record.  In rare cases where both sides of a trade appear under a single
  record the direction may be shown as "trade out" only; re-running after the
  API is updated will correct this automatically.
