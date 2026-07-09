"""Fantrax Transaction History Tracker - CLI entry point.

Usage
-----
Public league (no login required):

    python main.py \\
        --league-id  96igs4677sgjk7ol \\
        --team       "My Team Name" \\
        --output     my_team_history.png

Private league (cookie-based login, first run opens headless Chrome):

    python main.py \\
        --league-id  96igs4677sgjk7ol \\
        --team       "My Team Name" \\
        --username   you@example.com \\
        --password   secret \\
        --output     my_team_history.png

Options
-------
--league-id   Fantrax league ID (required).
--team        Partial or full fantasy team name (case-insensitive, required).
--username    Fantrax login e-mail (needed for private leagues).
--password    Fantrax password (needed for private leagues).
--output      Output PNG file path (default: transaction_history.png).
--max         Maximum number of raw transaction rows to fetch (default: 2000).
--show        Open the chart in a window after saving.
--years       Comma-separated list of years to include, e.g. 2022,2023,2024.
              Omit to include all available years.
--no-drops    Exclude drop transactions from the chart.
"""

from __future__ import annotations

import argparse
import sys

from fantraxapi import League

from auth import patch_league_auth
from chart import build_chart
from fetch import fetch_team_transactions


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fantrax_tracker",
        description="Build a visual transaction-history chart for your Fantrax team.",
    )
    parser.add_argument("--league-id", required=True, help="Fantrax league ID")
    parser.add_argument("--team", required=True, help="Fantasy team name (partial match OK)")
    parser.add_argument("--username", default="", help="Fantrax login e-mail (private leagues)")
    parser.add_argument("--password", default="", help="Fantrax password (private leagues)")
    parser.add_argument(
        "--output",
        default="transaction_history.png",
        help="Output file path (PNG). Use 'none' to skip saving.",
    )
    parser.add_argument("--max", dest="max_transactions", type=int, default=2000)
    parser.add_argument("--show", action="store_true", help="Display chart window")
    parser.add_argument(
        "--years",
        default="",
        help="Comma-separated years to include, e.g. 2022,2023,2024",
    )
    parser.add_argument(
        "--no-drops",
        action="store_true",
        dest="no_drops",
        help="Exclude drop transactions from the chart",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    league = League(args.league_id)

    if args.username or args.password:
        patch_league_auth(league, args.username, args.password)

    moves_by_year = fetch_team_transactions(
        league,
        team_name=args.team,
        max_transactions=args.max_transactions,
    )

    if not moves_by_year:
        print("No transactions found. Check your team name and league ID.", file=sys.stderr)
        sys.exit(1)

    if args.years.strip():
        wanted = {int(y.strip()) for y in args.years.split(",")}
        moves_by_year = {yr: s for yr, s in moves_by_year.items() if yr in wanted}
        if not moves_by_year:
            print(f"No transactions found for years: {args.years}", file=sys.stderr)
            sys.exit(1)

    if args.no_drops:
        for summary in moves_by_year.values():
            summary.drops.clear()

    print("\n── Transaction Summary ──────────────────────────────────────────")
    for year, summary in sorted(moves_by_year.items()):
        print(
            f"  {year}:  {len(summary.adds):3d} adds  "
            f"{len(summary.drops):3d} drops  "
            f"{len(summary.trade_ins):3d} trade-ins  "
            f"{len(summary.trade_outs):3d} trade-outs"
        )
    print()

    output_path = None if args.output.lower() == "none" else args.output
    build_chart(
        moves_by_year,
        team_name=args.team,
        output_path=output_path,
        show=args.show,
    )


if __name__ == "__main__":
    main()
