"""Generate a transaction-history tree chart from processed move data.

The chart mirrors the style in the reference image:
  • Years appear as labelled boxes on the left-hand edge.
  • Each year's moves spread horizontally across the canvas.
  • Adds are green, drops are red, trades-in are gold, trades-out are salmon.
  • Players that were part of the same trade are connected by a curved line.
  • The resulting figure is saved as a PNG (or displayed interactively).
"""

from __future__ import annotations

import math
import textwrap
from collections import defaultdict
from typing import Optional

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import networkx as nx
from matplotlib.patches import FancyBboxPatch

from fetch import (
    PlayerMove,
    T_ADD,
    T_DROP,
    T_TRADE_IN,
    T_TRADE_OUT,
    YearSummary,
    build_trade_pairs,
)


# ──────────────────────────────────────────────────────────────────────────────
# Colour / style constants
# ──────────────────────────────────────────────────────────────────────────────
YEAR_PALETTE = [
    "#d0e8d0",  # soft green  (oldest)
    "#fff3cc",  # pale yellow
    "#d0e4f7",  # sky blue
    "#fce4d6",  # peach
    "#e8d5f5",  # lavender
    "#d6f0ea",  # mint
    "#ffd6d6",  # rose
    "#ddeeff",  # ice blue  (newest)
]

TYPE_EDGE_COLOR = {
    T_ADD: "#2e7d32",
    T_DROP: "#c62828",
    T_TRADE_IN: "#b8860b",
    T_TRADE_OUT: "#8b4513",
}
TYPE_FACE_COLOR = {
    T_ADD: "#c8e6c9",
    T_DROP: "#ffcdd2",
    T_TRADE_IN: "#fff9c4",
    T_TRADE_OUT: "#ffe0b2",
}

YEAR_BOX_COLOR = "#e0e0e0"
YEAR_BOX_EDGE = "#888888"
YEAR_BOX_WIDTH = 0.9
YEAR_BOX_HEIGHT = 0.5
NODE_WIDTH = 1.6
NODE_HEIGHT = 0.38
H_SPACING = 1.9       # horizontal gap between nodes within a year row
V_SPACING = 2.2       # vertical gap between year rows
YEAR_X = 0.0          # x-coordinate of the year label column
NODE_X_START = 1.6    # x-coordinate where the first node in a row starts


# ──────────────────────────────────────────────────────────────────────────────
# Layout helpers
# ──────────────────────────────────────────────────────────────────────────────

def _wrap_name(name: str, max_width: int = 14) -> str:
    """Wrap a long player name to at most two lines."""
    if len(name) <= max_width:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    # First name initial + last name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _layout_year_row(
    moves: list[PlayerMove],
    year_index: int,
    sort_by_type: bool = True,
) -> dict[str, tuple[float, float]]:
    """Assign (x, y) coordinates to every move in one year row.

    Moves are optionally sorted: TRADE_IN first, then ADD, then TRADE_OUT,
    then DROP – matching the visual convention of the reference image.
    """
    if sort_by_type:
        order = {T_TRADE_IN: 0, T_ADD: 1, T_TRADE_OUT: 2, T_DROP: 3}
        moves = sorted(moves, key=lambda m: order.get(m.move_type, 9))

    y = -year_index * V_SPACING
    positions: dict[str, tuple[float, float]] = {}
    for i, move in enumerate(moves):
        x = NODE_X_START + i * H_SPACING
        # Use a composite key so two moves for the same player name don't clash
        key = _node_key(move)
        positions[key] = (x, y)
    return positions


def _node_key(move: PlayerMove) -> str:
    return f"{move.tx_id}_{move.player_id}_{move.move_type}"


# ──────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _draw_year_label(ax, x: float, y: float, year: int, color: str) -> None:
    box = FancyBboxPatch(
        (x - YEAR_BOX_WIDTH / 2, y - YEAR_BOX_HEIGHT / 2),
        YEAR_BOX_WIDTH,
        YEAR_BOX_HEIGHT,
        boxstyle="round,pad=0.05",
        linewidth=1.2,
        edgecolor=YEAR_BOX_EDGE,
        facecolor=color,
        zorder=3,
    )
    ax.add_patch(box)
    ax.text(
        x,
        y,
        str(year),
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        zorder=4,
    )


def _draw_player_node(
    ax,
    x: float,
    y: float,
    move: PlayerMove,
) -> None:
    face = TYPE_FACE_COLOR[move.move_type]
    edge = TYPE_EDGE_COLOR[move.move_type]
    label = _wrap_name(move.player_name)

    box = FancyBboxPatch(
        (x - NODE_WIDTH / 2, y - NODE_HEIGHT / 2),
        NODE_WIDTH,
        NODE_HEIGHT,
        boxstyle="round,pad=0.04",
        linewidth=1.0,
        edgecolor=edge,
        facecolor=face,
        zorder=3,
    )
    ax.add_patch(box)
    ax.text(
        x,
        y,
        label,
        ha="center",
        va="center",
        fontsize=6,
        zorder=4,
    )


def _draw_trade_connector(
    ax,
    pos_a: tuple[float, float],
    pos_b: tuple[float, float],
) -> None:
    """Draw a curved line connecting two nodes in the same trade."""
    x1, y1 = pos_a
    x2, y2 = pos_b
    # Use a simple arc via ConnectionPatch
    ax.annotate(
        "",
        xy=(x2, y2 + NODE_HEIGHT / 2),
        xytext=(x1, y1 - NODE_HEIGHT / 2),
        arrowprops=dict(
            arrowstyle="-",
            color="#999999",
            lw=0.8,
            connectionstyle="arc3,rad=0.2",
        ),
        zorder=2,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def build_chart(
    moves_by_year: dict[int, YearSummary],
    team_name: str,
    output_path: Optional[str] = "transaction_history.png",
    show: bool = False,
    dpi: int = 150,
) -> None:
    """Render and optionally save the transaction-history chart.

    Args:
        moves_by_year: Output of ``fetch.fetch_team_transactions()``.
        team_name: Used in the chart title.
        output_path: File path for the saved PNG.  ``None`` skips saving.
        show: If ``True``, call ``plt.show()`` after rendering.
        dpi: Resolution of the saved image.
    """
    if not moves_by_year:
        print("No transaction data to plot.")
        return

    years = sorted(moves_by_year.keys())
    trade_pairs = build_trade_pairs(moves_by_year)

    # ── Build position map ────────────────────────────────────────────────────
    all_positions: dict[str, tuple[float, float]] = {}
    moves_per_year: dict[int, list[PlayerMove]] = {}

    for idx, year in enumerate(years):
        summary = moves_by_year[year]
        all_moves = summary.all_moves
        moves_per_year[year] = all_moves
        row_pos = _layout_year_row(all_moves, idx)
        all_positions.update(row_pos)

    # ── Figure size ───────────────────────────────────────────────────────────
    max_moves = max((len(v) for v in moves_per_year.values()), default=1)
    fig_width = max(16, NODE_X_START + max_moves * H_SPACING + 2)
    fig_height = max(8, len(years) * V_SPACING + 2)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_aspect("equal")
    ax.axis("off")

    title = f"{team_name} – Transaction History"
    ax.set_title(title, fontsize=14, fontweight="bold", pad=16)

    # ── Draw year labels ──────────────────────────────────────────────────────
    for idx, year in enumerate(years):
        y_coord = -idx * V_SPACING
        color = YEAR_PALETTE[idx % len(YEAR_PALETTE)]
        _draw_year_label(ax, YEAR_X, y_coord, year, color)

    # ── Draw player nodes ─────────────────────────────────────────────────────
    for idx, year in enumerate(years):
        for move in moves_per_year[year]:
            key = _node_key(move)
            if key not in all_positions:
                continue
            x, y = all_positions[key]
            _draw_player_node(ax, x, y, move)

    # ── Draw trade connectors ─────────────────────────────────────────────────
    for tx_id, moves_in_tx in trade_pairs.items():
        trade_moves = [m for m in moves_in_tx if m.move_type in (T_TRADE_IN, T_TRADE_OUT)]
        ins = [m for m in trade_moves if m.move_type == T_TRADE_IN]
        outs = [m for m in trade_moves if m.move_type == T_TRADE_OUT]
        # Connect each TRADE_IN to each TRADE_OUT in the same transaction
        for m_in in ins:
            for m_out in outs:
                key_in = _node_key(m_in)
                key_out = _node_key(m_out)
                if key_in in all_positions and key_out in all_positions:
                    _draw_trade_connector(ax, all_positions[key_out], all_positions[key_in])

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_ADD], edgecolor=TYPE_EDGE_COLOR[T_ADD], label="Add"),
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_DROP], edgecolor=TYPE_EDGE_COLOR[T_DROP], label="Drop"),
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_TRADE_IN], edgecolor=TYPE_EDGE_COLOR[T_TRADE_IN], label="Trade In"),
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_TRADE_OUT], edgecolor=TYPE_EDGE_COLOR[T_TRADE_OUT], label="Trade Out"),
    ]
    ax.legend(
        handles=legend_items,
        loc="upper right",
        fontsize=8,
        framealpha=0.9,
        title="Transaction Type",
        title_fontsize=8,
    )

    # ── Auto-scale axes ───────────────────────────────────────────────────────
    ax.autoscale_view()
    margin_x = H_SPACING
    margin_y = V_SPACING * 0.6
    xs = [p[0] for p in all_positions.values()]
    ys = [p[1] for p in all_positions.values()]
    if xs and ys:
        ax.set_xlim(min(xs) - margin_x, max(xs) + margin_x)
        ax.set_ylim(min(ys) - margin_y, max(ys) + margin_y)

    plt.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        print(f"Chart saved to: {output_path}")

    if show:
        plt.show()

    plt.close(fig)
