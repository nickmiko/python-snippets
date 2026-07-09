"""Fetch and process Fantrax transaction history for a specific team.

Transaction type constants returned by the API:
  ADD      – waiver/free-agent add
  DROP     – drop
  TRADE    – trade (player either coming in or going out)
  CLAIM    – raw claim before the claimType is resolved (handled internally)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fantraxapi import League


# Transaction type constants used throughout the app
T_ADD = "ADD"
T_DROP = "DROP"
T_TRADE_IN = "TRADE_IN"
T_TRADE_OUT = "TRADE_OUT"

# Raw API codes mapped to our constants
_RAW_ADD_TYPES = {"WAIVER", "FREE_AGENT", "CLAIM"}
_RAW_DROP_TYPES = {"DROP"}
_RAW_TRADE_TYPES = {"TRADE"}


@dataclass
class PlayerMove:
    """A single player movement within one transaction."""

    player_name: str
    player_id: str
    move_type: str          # T_ADD / T_DROP / T_TRADE_IN / T_TRADE_OUT
    date: datetime
    year: int
    tx_id: str             # Links players that share the same transaction


@dataclass
class YearSummary:
    """All moves for a single season."""

    year: int
    adds: list[PlayerMove] = field(default_factory=list)
    drops: list[PlayerMove] = field(default_factory=list)
    trade_ins: list[PlayerMove] = field(default_factory=list)
    trade_outs: list[PlayerMove] = field(default_factory=list)

    @property
    def all_moves(self) -> list[PlayerMove]:
        return self.adds + self.drops + self.trade_ins + self.trade_outs


def _classify(raw_type: str) -> str:
    """Map a raw API transaction type to ADD, DROP, or TRADE.

    Trade direction (TRADE_IN / TRADE_OUT) is resolved later in
    ``_process_transaction`` once we know which team owns the record.
    """
    up = raw_type.upper()
    if up in _RAW_ADD_TYPES:
        return T_ADD
    if up in _RAW_DROP_TYPES:
        return T_DROP
    return "TRADE"  # resolved further in _process_transaction


def _process_transaction(
    tx,
    our_team_id: str,
) -> list[PlayerMove]:
    """Convert one ``Transaction`` object into a list of ``PlayerMove``s."""
    moves: list[PlayerMove] = []
    tx_team_id: str = tx.team.id if hasattr(tx.team, "id") else str(tx.team)

    for player in tx.players:
        raw = player.type.upper()
        if raw in _RAW_TRADE_TYPES:
            # Determine direction: the transaction is recorded once per trade
            # partner, so we always have ``tx.team`` as the team *sending* the
            # players listed.  Players sent *by our team* are TRADE_OUT; players
            # sent *by the other team* (i.e. received by us) are TRADE_IN.
            move_type = T_TRADE_OUT if tx_team_id == our_team_id else T_TRADE_IN
        elif raw in _RAW_ADD_TYPES:
            move_type = T_ADD
        elif raw in _RAW_DROP_TYPES:
            move_type = T_DROP
        else:
            # Unknown type – include as an add so it is visible
            move_type = T_ADD

        moves.append(
            PlayerMove(
                player_name=player.name,
                player_id=player.id,
                move_type=move_type,
                date=tx.date,
                year=tx.date.year,
                tx_id=tx.id,
            )
        )
    return moves


def fetch_team_transactions(
    league: League,
    team_name: str,
    max_transactions: int = 2000,
) -> dict[int, YearSummary]:
    """Fetch all transactions for a team and return them grouped by year.

    Args:
        league: Authenticated ``fantraxapi.League`` instance.
        team_name: Display name of the fantasy team to filter for (case-
            insensitive partial match is supported).
        max_transactions: Upper bound on the number of raw transaction rows to
            fetch from the API.  Increase if your league has many years of
            history.

    Returns:
        A dict mapping ``year`` → ``YearSummary`` sorted by year ascending.

    Raises:
        ValueError: If no team matching *team_name* is found in the league.
    """
    # Resolve team ID
    team_name_lower = team_name.lower()
    our_team = None
    for tid, team in league.team_lookup.items():
        if team_name_lower in team.name.lower():
            our_team = team
            our_team_id = tid
            break
    if our_team is None:
        available = [t.name for t in league.team_lookup.values()]
        raise ValueError(
            f"Team '{team_name}' not found. Available teams: {available}"
        )

    print(f"Fetching up to {max_transactions} transactions for '{our_team.name}'…")
    all_transactions = league.transactions(count=max_transactions)

    # First pass: collect tx_ids where our team was a trade participant so we
    # can identify the counterpart records (the other team's side of the trade,
    # which carries the players *we received*).
    our_trade_tx_ids: set[str] = {
        tx.id
        for tx in all_transactions
        if (tx.team.id if hasattr(tx.team, "id") else str(tx.team)) == our_team_id
        and any(p.type.upper() in _RAW_TRADE_TYPES for p in tx.players)
    }

    summaries: dict[int, YearSummary] = defaultdict(lambda: YearSummary(year=0))

    for tx in all_transactions:
        tx_team_id = tx.team.id if hasattr(tx.team, "id") else str(tx.team)
        is_our_tx = tx_team_id == our_team_id
        is_our_trade_counterpart = (
            not is_our_tx and tx.id in our_trade_tx_ids
        )

        # Skip transactions that don't involve our team at all
        if not is_our_tx and not is_our_trade_counterpart:
            continue

        moves = _process_transaction(tx, our_team_id)
        for move in moves:
            year = move.year
            if year not in summaries:
                summaries[year] = YearSummary(year=year)
            summary = summaries[year]
            if move.move_type == T_ADD:
                summary.adds.append(move)
            elif move.move_type == T_DROP:
                summary.drops.append(move)
            elif move.move_type == T_TRADE_IN:
                summary.trade_ins.append(move)
            elif move.move_type == T_TRADE_OUT:
                summary.trade_outs.append(move)

    return dict(sorted(summaries.items()))


def build_trade_pairs(
    moves_by_year: dict[int, YearSummary],
) -> dict[str, list[PlayerMove]]:
    """Group PlayerMoves by tx_id so trade partners can be drawn together.

    Returns:
        A dict mapping tx_id → list of PlayerMoves in that transaction.
    """
    groups: dict[str, list[PlayerMove]] = defaultdict(list)
    for summary in moves_by_year.values():
        for move in summary.all_moves:
            groups[move.tx_id].append(move)
    return dict(groups)
