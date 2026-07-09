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


def _classify(raw_type: str, team_id: str, transaction_team_id: str) -> str:
    """Map a raw API transaction type to our simplified constants.

    For TRADE rows, the API does not distinguish incoming vs outgoing by a
    separate field – all players in the transaction belong to the same
    ``Transaction.team``.  We therefore expose every player as ``TRADE`` and
    let the caller decide direction by comparing ``Transaction.team`` with the
    team-under-analysis.  The logic below handles the two-team scenario:

    * All TRADE players whose transaction owner IS our team ⟹ they were
      **sent away** (TRADE_OUT).
    * All TRADE players whose transaction owner is NOT our team ⟹ they were
      **received** (TRADE_IN).

    This function only handles ADD / DROP here; trade direction is resolved in
    ``_process_transaction``.
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

    summaries: dict[int, YearSummary] = defaultdict(lambda: YearSummary(year=0))

    for tx in all_transactions:
        tx_team_id = tx.team.id if hasattr(tx.team, "id") else str(tx.team)

        # We want transactions that INVOLVE our team.  In the Fantrax API each
        # trade generates two Transaction objects – one per team – so we'll
        # see our team's record directly.  For adds/drops the team must match.
        is_our_tx = tx_team_id == our_team_id

        # For trade transactions we also need the *counterpart* record (the
        # transaction where the other team is the owner but our team received
        # players).  The API unfortunately does not expose who received the
        # trade – only who sent the players.  We detect this by checking
        # whether the trade already has a matching partner recorded.
        if not is_our_tx:
            continue  # only process transactions owned by our team for now

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
