"""Fetch and classify Fantrax transaction history for a specific team.

fantraxapi's ``League.transactions()`` / ``api.get_transaction_history()``
call the ``getTransactionDetailsHistory`` endpoint with no ``view`` or
``team`` filter and assume a fixed cell layout (``cells[0]`` = team,
``cells[1]`` = date). Neither holds up against a real league:

- The endpoint only returns one transaction category per call. With no
  ``view`` argument the server defaults to ``CLAIM_DROP`` — trades never
  come back at all unless you separately request ``view="TRADE"``.
- Row cell layout depends on transaction type, not a fixed schema. Claim/drop
  rows carry ``team``/``bid``/``priority``/``date``/``week`` cells (in that
  order, keyed by ``"key"``); trade rows carry ``from``/``to``/``date``/
  ``week`` cells instead — there is no ``"team"`` cell at all, and no
  ``transactionCode`` field either. Indexing by position instead of by
  ``"key"`` silently mis-parses claim rows and misses trades entirely.
- A multi-row transaction (e.g. a claim that also drops a player) renders
  its ``team``/``date``/``from``/``to`` cells only on the first row of the
  group (an HTML-rowspan artifact) — later rows in the same ``txSetId``
  omit them.

This module talks to the raw endpoint directly, keyed by ``view``, scoped
server-side with a ``team`` filter (both trade sides included), and reads
every cell by its ``"key"`` rather than its position.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from fantraxapi import League
from fantraxapi.api import Method
from fantraxapi.api import request as fantrax_request

T_ADD = "ADD"
T_DROP = "DROP"
T_TRADE_IN = "TRADE_IN"
T_TRADE_OUT = "TRADE_OUT"

_VIEW_CLAIM_DROP = "CLAIM_DROP"
_VIEW_TRADE = "TRADE"

_PAGE_SIZE = 500


@dataclass
class PlayerMove:
    """A single player's movement within one transaction."""

    player_name: str
    player_id: str
    move_type: str  # T_ADD / T_DROP / T_TRADE_IN / T_TRADE_OUT
    date: datetime
    year: int
    tx_id: str  # Shared by every player in the same transaction (links trade partners)


@dataclass
class YearSummary:
    """All of a team's moves for a single season."""

    year: int
    adds: list[PlayerMove] = field(default_factory=list)
    drops: list[PlayerMove] = field(default_factory=list)
    trade_ins: list[PlayerMove] = field(default_factory=list)
    trade_outs: list[PlayerMove] = field(default_factory=list)

    @property
    def all_moves(self) -> list[PlayerMove]:
        return self.adds + self.drops + self.trade_ins + self.trade_outs


def _cell(row: dict, key: str) -> dict | None:
    return next((c for c in row.get("cells", []) if c.get("key") == key), None)


def _parse_date(date_str: str) -> datetime:
    # Fantrax renders e.g. "Thu Jul 09, 2026, 10:00AM"; strptime's %p is
    # case-insensitive on glibc/Linux but not guaranteed elsewhere, so
    # normalize the am/pm case before parsing.
    return datetime.strptime(date_str.upper(), "%a %b %d, %Y, %I:%M%p")


def _fetch_view_rows(league: League, view: str, team_id: str, max_transactions: int) -> list[dict]:
    """Fetch every raw row for one transaction view, scoped to one team.

    Paginates until either the server reports no more pages or
    *max_transactions* rows have been collected.
    """
    rows: list[dict] = []
    page = 1
    page_size = min(_PAGE_SIZE, max_transactions) or 1
    while len(rows) < max_transactions:
        response = fantrax_request(
            league,
            Method(
                "getTransactionDetailsHistory",
                maxResultsPerPage=str(page_size),
                view=view,
                team=team_id,
                pageNumber=str(page),
            ),
        )
        page_rows = response["table"]["rows"]
        rows.extend(page_rows)
        total_pages = response.get("paginatedResultSet", {}).get("totalNumPages", 1)
        if page >= total_pages or not page_rows:
            break
        page += 1
    return rows[:max_transactions]


def _tx_date(tx_rows: list[dict]) -> datetime | None:
    """Find the transaction date, which only the first row in a group carries."""
    for row in tx_rows:
        date_cell = _cell(row, "date")
        if date_cell and date_cell.get("content"):
            try:
                return _parse_date(date_cell["content"])
            except ValueError:
                continue
    return None


def _moves_from_claim_drop(tx_rows: list[dict], tx_id: str, date: datetime) -> list[PlayerMove]:
    moves: list[PlayerMove] = []
    for row in tx_rows:
        code = row.get("transactionCode", "")
        if code == "CLAIM":
            move_type = T_ADD
        elif code == "DROP":
            move_type = T_DROP
        else:
            continue  # e.g. lineup-adjacent codes that don't represent a roster add/drop

        try:
            scorer = row["scorer"]
            moves.append(
                PlayerMove(
                    player_name=scorer["name"],
                    player_id=scorer["scorerId"],
                    move_type=move_type,
                    date=date,
                    year=date.year,
                    tx_id=tx_id,
                )
            )
        except (KeyError, TypeError):
            continue
    return moves


def _moves_from_trade(tx_rows: list[dict], tx_id: str, date: datetime, our_team_id: str) -> list[PlayerMove]:
    moves: list[PlayerMove] = []
    for row in tx_rows:
        from_cell = _cell(row, "from")
        to_cell = _cell(row, "to")
        if from_cell is None or to_cell is None:
            continue

        if to_cell.get("teamId") == our_team_id:
            move_type = T_TRADE_IN
        elif from_cell.get("teamId") == our_team_id:
            move_type = T_TRADE_OUT
        else:
            continue  # a leg of a multi-team trade that doesn't touch our team

        try:
            scorer = row["scorer"]
            moves.append(
                PlayerMove(
                    player_name=scorer["name"],
                    player_id=scorer["scorerId"],
                    move_type=move_type,
                    date=date,
                    year=date.year,
                    tx_id=tx_id,
                )
            )
        except (KeyError, TypeError):
            continue
    return moves


def fetch_team_transactions(
    league: League,
    team_name: str,
    max_transactions: int = 2000,
    tx_prefix: str = "",
) -> dict[int, YearSummary]:
    """Fetch all transactions involving a team, grouped by year.

    Args:
        league: A ``fantraxapi.League`` instance (already authenticated if
            the league is private).
        team_name: Fantasy team name to filter for. Case-insensitive partial
            match, e.g. ``"Yeti"`` matches ``"The Abominable Yeti"``.
        max_transactions: Upper bound on raw transaction rows fetched per
            view (claim/drop and trade are fetched separately). Increase
            this if your team has many seasons of history and older
            transactions seem to be missing.
        tx_prefix: Prepended to every tx_id. Fantrax assigns a new league ID
            each season for non-dynasty leagues, so charting multiple seasons
            means fetching from several League instances and merging the
            results (see ``merge_year_summaries``) - tx_ids are only unique
            *within* one league's API responses, so without a per-league
            prefix, two different seasons' transactions could collide on the
            same tx_id and get incorrectly treated as one transaction.

    Returns:
        Dict of year -> YearSummary, sorted ascending by year.

    Raises:
        ValueError: If no team matches *team_name*.
    """
    team_name_lower = team_name.lower()
    our_team_id = None
    our_team_display_name = None
    for tid, team in league.team_lookup.items():
        if team_name_lower in team.name.lower():
            our_team_id = tid
            our_team_display_name = team.name
            break
    if our_team_id is None:
        available = sorted(t.name for t in league.team_lookup.values())
        raise ValueError(f"Team '{team_name}' not found. Available teams: {available}")

    print(f"Fetching transactions for '{our_team_display_name}'...")
    claim_drop_rows = _fetch_view_rows(league, _VIEW_CLAIM_DROP, our_team_id, max_transactions)
    trade_rows = _fetch_view_rows(league, _VIEW_TRADE, our_team_id, max_transactions)
    print(f"  {len(claim_drop_rows)} claim/drop rows, {len(trade_rows)} trade rows")

    summaries: dict[int, YearSummary] = {}

    def _summary_for(year: int) -> YearSummary:
        if year not in summaries:
            summaries[year] = YearSummary(year=year)
        return summaries[year]

    for rows, builder in (
        (claim_drop_rows, lambda tx_rows, tx_id, date: _moves_from_claim_drop(tx_rows, tx_id, date)),
        (trade_rows, lambda tx_rows, tx_id, date: _moves_from_trade(tx_rows, tx_id, date, our_team_id)),
    ):
        rows_by_tx: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            raw_tx_id = row.get("txSetId")
            if raw_tx_id:
                rows_by_tx[f"{tx_prefix}{raw_tx_id}"].append(row)

        for tx_id, tx_rows in rows_by_tx.items():
            date = _tx_date(tx_rows)
            if date is None:
                continue  # malformed group; skip rather than guess a date
            for move in builder(tx_rows, tx_id, date):
                summary = _summary_for(move.year)
                if move.move_type == T_ADD:
                    summary.adds.append(move)
                elif move.move_type == T_DROP:
                    summary.drops.append(move)
                elif move.move_type == T_TRADE_IN:
                    summary.trade_ins.append(move)
                elif move.move_type == T_TRADE_OUT:
                    summary.trade_outs.append(move)

    return dict(sorted(summaries.items()))


def merge_year_summaries(*sources: dict[int, YearSummary]) -> dict[int, YearSummary]:
    """Combine several year->YearSummary maps (e.g. one per season's league ID).

    Returns:
        A single dict of year -> YearSummary, sorted ascending by year.
    """
    merged: dict[int, YearSummary] = {}
    for source in sources:
        for year, summary in source.items():
            target = merged.setdefault(year, YearSummary(year=year))
            target.adds.extend(summary.adds)
            target.drops.extend(summary.drops)
            target.trade_ins.extend(summary.trade_ins)
            target.trade_outs.extend(summary.trade_outs)
    return dict(sorted(merged.items()))


def build_trade_pairs(moves_by_year: dict[int, YearSummary]) -> dict[str, list[PlayerMove]]:
    """Group every PlayerMove by tx_id so trade partners can be linked visually.

    Returns:
        Dict of tx_id -> list of PlayerMoves that share that transaction.
    """
    groups: dict[str, list[PlayerMove]] = defaultdict(list)
    for summary in moves_by_year.values():
        for move in summary.all_moves:
            groups[move.tx_id].append(move)
    return dict(groups)
