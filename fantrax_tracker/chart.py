"""Generate a transaction-history tree chart from processed move data.

The chart matches the style in the reference image:
  • Year labels as coloured boxes on the left edge; one horizontal band per year.
  • Each player is a rounded-rect node coloured by transaction type.
  • Trade partners (TRADE_OUT → TRADE_IN, same tx_id) are connected by vertical
    lines so the tree flows downward through the years.
  • Nodes in the same trade transaction are grouped together horizontally so
    their connecting lines stay short and tidy.
  • The resulting figure is saved as a PNG (or displayed interactively).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

import networkx as nx
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
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
    "#d0e8d0",  # soft green
    "#fff3cc",  # pale yellow
    "#d0e4f7",  # sky blue
    "#fce4d6",  # peach
    "#e8d5f5",  # lavender
    "#d6f0ea",  # mint
    "#ffd6d6",  # rose
    "#ddeeff",  # ice blue
]

TYPE_EDGE_COLOR = {
    T_ADD:       "#2e7d32",
    T_DROP:      "#c62828",
    T_TRADE_IN:  "#b8860b",
    T_TRADE_OUT: "#8b4513",
}
TYPE_FACE_COLOR = {
    T_ADD:       "#c8e6c9",
    T_DROP:      "#ffcdd2",
    T_TRADE_IN:  "#fff9c4",
    T_TRADE_OUT: "#ffe0b2",
}

# Layout tunables
NODE_W = 1.5        # node half-width
NODE_H = 0.28       # node half-height
H_GAP = 1.8         # minimum horizontal gap between nodes
V_GAP = 2.4         # vertical gap between year-row centres
YEAR_LABEL_X = 0.0
NODE_START_X = 1.4

YEAR_BOX_W = 0.85
YEAR_BOX_H = 0.40
CONNECTOR_COLOR = "#aaaaaa"
CONNECTOR_LW = 0.8

# Within a year band, TRADE_OUT sits slightly above centre and TRADE_IN
# slightly below, so the TRADE_OUT → TRADE_IN edges are always visible.
SUB_Y: dict[str, float] = {
    T_TRADE_OUT:  0.35,   # above year centre
    T_ADD:        0.0,
    T_DROP:       0.0,
    T_TRADE_IN:  -0.35,   # below year centre (connected to by TRADE_OUT)
}


# ──────────────────────────────────────────────────────────────────────────────
# Name helper
# ──────────────────────────────────────────────────────────────────────────────

def _wrap_name(name: str, max_chars: int = 14) -> str:
    """Abbreviate first name to initial when name is long."""
    if len(name) <= max_chars:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _node_key(move: PlayerMove) -> str:
    return f"{move.tx_id}_{move.player_id}_{move.move_type}"


# ──────────────────────────────────────────────────────────────────────────────
# Graph construction
# ──────────────────────────────────────────────────────────────────────────────

def _build_graph(
    moves_by_year: dict[int, YearSummary],
    trade_pairs: dict[str, list[PlayerMove]],
) -> nx.DiGraph:
    """Build a directed graph where edges represent transaction connections.

    Each node stores the ``PlayerMove`` under key ``'move'``.

    Two kinds of edges are added:

    1. **Within-transaction trade edges** (TRADE_OUT → TRADE_IN, same tx_id):
       These connect the player sent away with the player(s) received in the
       same trade, so the tree flows "what did I trade them for?".

    2. **Cross-year lineage edges** (ADD/TRADE_IN → TRADE_OUT/DROP):
       If a player acquired in year N later departs (trade-out or drop) in
       year N+k, their acquisition node connects to their departure node.
       This creates the long vertical chains visible in the reference image
       where a roster slot's entire history flows downward year by year.
    """
    G: nx.DiGraph = nx.DiGraph()

    for summary in moves_by_year.values():
        for move in summary.all_moves:
            key = _node_key(move)
            G.add_node(key, move=move)

    # ── 1. Within-transaction trade edges ────────────────────────────────────
    for moves_in_tx in trade_pairs.values():
        outs = [m for m in moves_in_tx if m.move_type == T_TRADE_OUT]
        ins  = [m for m in moves_in_tx if m.move_type == T_TRADE_IN]
        for m_out in outs:
            for m_in in ins:
                k_out = _node_key(m_out)
                k_in  = _node_key(m_in)
                if G.has_node(k_out) and G.has_node(k_in):
                    G.add_edge(k_out, k_in)

    # ── 2. Cross-year lineage edges ───────────────────────────────────────────
    # Walk through every year in order and track the most-recent acquisition
    # key for each player_id.  When we see that player depart, draw an edge
    # from their acquisition node to their departure node.
    last_acq: dict[str, str] = {}  # player_id → node key of latest acquisition

    for year in sorted(moves_by_year):
        summary = moves_by_year[year]
        # Sort within the year by date so same-day order is deterministic
        all_moves = sorted(summary.all_moves, key=lambda m: m.date)
        for move in all_moves:
            key = _node_key(move)
            pid = move.player_id
            if move.move_type in (T_ADD, T_TRADE_IN):
                last_acq[pid] = key
            elif move.move_type in (T_DROP, T_TRADE_OUT):
                if pid in last_acq:
                    acq_key = last_acq.pop(pid)
                    if acq_key != key and G.has_node(acq_key):
                        G.add_edge(acq_key, key)

    return G


# ──────────────────────────────────────────────────────────────────────────────
# Tree layout
# ──────────────────────────────────────────────────────────────────────────────

def _tree_layout(
    G: nx.DiGraph,
    year_y: dict[int, float],
) -> dict[str, tuple[float, float]]:
    """Assign (x, y) to every node using a Reingold-Tilford-style algorithm.

    The base y-coordinate is fixed by the node's year; a small sub-y offset
    is added so that TRADE_OUT nodes sit above the year centre and TRADE_IN
    nodes sit below it — making within-year trade edges clearly visible.

    The x-coordinate is determined recursively so that:
      • leaves are placed at successive integer slots,
      • internal nodes are centred over their children,
      • subtrees never overlap.

    Nodes that have no trade-graph connection (plain adds and drops) are placed
    after all tree-connected nodes, also at successive slots.
    """
    positions: dict[str, tuple[float, float]] = {}
    slot: list[float] = [0.0]

    def _y(move: PlayerMove) -> float:
        return year_y[move.year] + SUB_Y.get(move.move_type, 0.0)

    def _place(node: str) -> float:
        """Return the x-coordinate assigned to *node*."""
        children = list(G.successors(node))
        if not children:
            x = slot[0]
            slot[0] += 1.0
        else:
            child_xs = [_place(c) for c in children]
            x = (child_xs[0] + child_xs[-1]) / 2.0

        move: PlayerMove = G.nodes[node]["move"]
        positions[node] = (x, _y(move))
        return x

    # Process trade-chain roots first (no incoming edges, has outgoing)
    roots_trade = [
        n for n in G.nodes
        if G.in_degree(n) == 0 and G.out_degree(n) > 0
    ]
    for root in roots_trade:
        _place(root)

    # Isolated nodes (adds / drops / standalone with no graph edges) grouped
    # by year so same-year nodes stay together in the layout
    isolated = [n for n in G.nodes if G.degree(n) == 0]
    by_year: dict[int, list[str]] = defaultdict(list)
    for n in isolated:
        year = G.nodes[n]["move"].year
        by_year[year].append(n)

    for year in sorted(by_year):
        for n in by_year[year]:
            move: PlayerMove = G.nodes[n]["move"]
            positions[n] = (slot[0], _y(move))
            slot[0] += 1.0

    # Catch any remaining orphan nodes
    for n in G.nodes:
        if n not in positions:
            move: PlayerMove = G.nodes[n]["move"]
            positions[n] = (slot[0], _y(move))
            slot[0] += 1.0

    # Scale x by H_GAP and shift right to clear the year-label column
    return {
        k: (NODE_START_X + x * H_GAP, y)
        for k, (x, y) in positions.items()
    }


# ──────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _draw_year_label(
    ax,
    x: float,
    y: float,
    year: int,
    color: str,
) -> None:
    box = FancyBboxPatch(
        (x - YEAR_BOX_W / 2, y - YEAR_BOX_H / 2),
        YEAR_BOX_W,
        YEAR_BOX_H,
        boxstyle="round,pad=0.05",
        linewidth=1.2,
        edgecolor="#888888",
        facecolor=color,
        zorder=3,
    )
    ax.add_patch(box)
    ax.text(
        x, y, str(year),
        ha="center", va="center",
        fontsize=8, fontweight="bold",
        zorder=4,
    )


def _draw_node(ax, x: float, y: float, move: PlayerMove) -> None:
    face = TYPE_FACE_COLOR[move.move_type]
    edge = TYPE_EDGE_COLOR[move.move_type]
    label = _wrap_name(move.player_name)

    box = FancyBboxPatch(
        (x - NODE_W / 2, y - NODE_H / 2),
        NODE_W,
        NODE_H,
        boxstyle="round,pad=0.04",
        linewidth=0.9,
        edgecolor=edge,
        facecolor=face,
        zorder=3,
    )
    ax.add_patch(box)
    ax.text(
        x, y, label,
        ha="center", va="center",
        fontsize=5.5,
        zorder=4,
    )


def _draw_edge(
    ax,
    pos_parent: tuple[float, float],
    pos_child: tuple[float, float],
) -> None:
    """Draw an elbow connector from the bottom of the parent to the top of the child.

    When the parent and child share the same year band (same base y), the
    sub-y offsets ensure y1 > y2, so the connector is a short vertical
    down-and-back jog that is always clearly visible.
    """
    x1, y1 = pos_parent
    x2, y2 = pos_child
    # Start at bottom of parent node, end at top of child node
    y_start = y1 - NODE_H
    y_end   = y2 + NODE_H
    mid_y   = (y_start + y_end) / 2.0
    ax.plot(
        [x1, x1, x2, x2],
        [y_start, mid_y, mid_y, y_end],
        color=CONNECTOR_COLOR,
        linewidth=CONNECTOR_LW,
        solid_capstyle="round",
        zorder=1,
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
    """Render and optionally save the transaction-history tree chart.

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

    # ── Year → y-coordinate mapping ───────────────────────────────────────────
    year_y: dict[int, float] = {
        year: -idx * V_GAP for idx, year in enumerate(years)
    }

    # ── Build graph & compute layout ──────────────────────────────────────────
    G = _build_graph(moves_by_year, trade_pairs)
    positions = _tree_layout(G, year_y)

    # ── Figure sizing ─────────────────────────────────────────────────────────
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    fig_w = max(20, (max(xs) - NODE_START_X) + 4) if xs else 20
    fig_h = max(8, len(years) * V_GAP + 2)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(
        f"{team_name} — Transaction History",
        fontsize=13, fontweight="bold", pad=14,
    )

    # ── Year background bands ─────────────────────────────────────────────────
    # Each band spans ±(V_GAP/2 + max_sub_y) around the year centre so that
    # the sub-y-offset nodes (TRADE_OUT/IN) still fall inside the band.
    x_min = YEAR_LABEL_X - YEAR_BOX_W
    x_max = max(xs) + NODE_W if xs else 20
    half_band = V_GAP / 2 + max(abs(v) for v in SUB_Y.values()) + NODE_H + 0.05
    for idx, year in enumerate(years):
        y_center = year_y[year]
        color = YEAR_PALETTE[idx % len(YEAR_PALETTE)]
        band = mpatches.FancyBboxPatch(
            (x_min, y_center - half_band),
            x_max - x_min,
            half_band * 2,
            boxstyle="square,pad=0",
            linewidth=0,
            facecolor=color,
            alpha=0.18,
            zorder=0,
        )
        ax.add_patch(band)

    # ── Year labels ───────────────────────────────────────────────────────────
    for idx, year in enumerate(years):
        color = YEAR_PALETTE[idx % len(YEAR_PALETTE)]
        _draw_year_label(ax, YEAR_LABEL_X, year_y[year], year, color)

    # ── Edges ─────────────────────────────────────────────────────────────────
    for parent, child in G.edges():
        if parent in positions and child in positions:
            _draw_edge(ax, positions[parent], positions[child])

    # ── Player nodes ──────────────────────────────────────────────────────────
    for node, (x, y) in positions.items():
        move: PlayerMove = G.nodes[node]["move"]
        _draw_node(ax, x, y, move)

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_ADD],       edgecolor=TYPE_EDGE_COLOR[T_ADD],       label="Add"),
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_DROP],      edgecolor=TYPE_EDGE_COLOR[T_DROP],      label="Drop"),
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[T_TRADE_IN],  edgecolor=TYPE_EDGE_COLOR[T_TRADE_IN],  label="Trade In"),
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

    # ── Axis limits ───────────────────────────────────────────────────────────
    if xs and ys:
        margin_x = H_GAP
        margin_y = V_GAP * 0.7
        ax.set_xlim(x_min - margin_x * 0.5, max(xs) + margin_x)
        ax.set_ylim(min(ys) - margin_y, max(ys) + margin_y)

    plt.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        print(f"Chart saved to: {output_path}")

    if show:
        plt.show()

    plt.close(fig)

