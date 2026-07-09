"""Render a transaction-history tree chart from processed move data.

Matches the reference layout:
  - One horizontal, colour-banded row per year, with a year label on the left.
  - Each player move is a rounded-rect node, coloured by transaction type.
  - Trade partners (TRADE_OUT <-> TRADE_IN sharing a tx_id) are connected.
  - A player's acquisition (add/trade-in) is connected down to their eventual
    departure (drop/trade-out) in a later year, forming lineage chains.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
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

YEAR_PALETTE = [
    "#d0e8d0",  # green
    "#fff3cc",  # pale yellow
    "#d0e4f7",  # sky blue
    "#fce4d6",  # peach
    "#e8d5f5",  # lavender
    "#d6f0ea",  # mint
    "#ffd6d6",  # rose
    "#ddeeff",  # ice blue
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

NODE_W = 1.5
NODE_H = 0.28
H_GAP = 1.8
V_GAP = 2.4
YEAR_LABEL_X = 0.0
NODE_START_X = 1.4
YEAR_BOX_W = 0.85
YEAR_BOX_H = 0.40
CONNECTOR_COLOR = "#aaaaaa"
CONNECTOR_LW = 0.8

# Offset trade-out/trade-in nodes above/below the year centre so their
# connecting edge is visibly a short jog rather than overlapping exactly.
SUB_Y: dict[str, float] = {
    T_TRADE_OUT: 0.35,
    T_ADD: 0.0,
    T_DROP: 0.0,
    T_TRADE_IN: -0.35,
}


def _wrap_name(name: str, max_chars: int = 14) -> str:
    if len(name) <= max_chars:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _node_key(move: PlayerMove) -> str:
    return f"{move.tx_id}_{move.player_id}_{move.move_type}"


def _build_graph(
    moves_by_year: dict[int, YearSummary],
    trade_pairs: dict[str, list[PlayerMove]],
) -> nx.DiGraph:
    """Build a directed graph linking related PlayerMove nodes.

    Two edge kinds:
      1. Within-transaction trade edges: TRADE_OUT -> TRADE_IN for the same
         tx_id, showing what was traded for what.
      2. Cross-year lineage edges: a player's acquisition (ADD/TRADE_IN)
         connects down to their later departure (DROP/TRADE_OUT), tracing a
         roster slot's history year over year.
    """
    graph: nx.DiGraph = nx.DiGraph()

    for summary in moves_by_year.values():
        for move in summary.all_moves:
            graph.add_node(_node_key(move), move=move)

    for moves_in_tx in trade_pairs.values():
        outs = [m for m in moves_in_tx if m.move_type == T_TRADE_OUT]
        ins = [m for m in moves_in_tx if m.move_type == T_TRADE_IN]
        for m_out in outs:
            for m_in in ins:
                # kind="trade" edges are drawn but never drive layout (see
                # _tree_layout): an N-for-M trade wires every out to every
                # in, so any M > 1 gives multiple trade-in nodes the exact
                # same predecessor set - deriving position from them would
                # collapse those nodes onto the same x/y.
                graph.add_edge(_node_key(m_out), _node_key(m_in), kind="trade")

    last_acquisition: dict[str, str] = {}  # player_id -> node key of latest acquisition
    for year in sorted(moves_by_year):
        for move in sorted(moves_by_year[year].all_moves, key=lambda m: m.date):
            key = _node_key(move)
            pid = move.player_id
            if move.move_type in (T_ADD, T_TRADE_IN):
                last_acquisition[pid] = key
            elif move.move_type in (T_DROP, T_TRADE_OUT):
                acq_key = last_acquisition.pop(pid, None)
                if acq_key and acq_key != key:
                    # Each player has at most one active acquisition at a
                    # time, so every node has at most one kind="lineage"
                    # parent and one such child - a simple chain, never a
                    # fan-in - which is what makes it safe to use for layout.
                    graph.add_edge(acq_key, key, kind="lineage")

    return graph


def _cross_year_lineage(graph: nx.DiGraph, node: str, direction: str) -> list[str]:
    """kind="lineage" neighbours of *node* that fall in a different year.

    Same-year lineage edges (added and dropped within one season) are
    excluded on purpose: T_ADD and T_DROP share the same SUB_Y offset, so a
    same-year acquisition and departure sit at the same y. If x-derivation
    also forced them onto the same x (as cross-year pairs correctly do, to
    draw a clean vertical line down through the years), the two nodes would
    render at the exact same point, completely overlapping. Excluding them
    here just means they fall through to the transaction-clustering pass
    below and get distinct x slots; the edge itself is still drawn.
    """
    edges = graph.out_edges(node, data=True) if direction == "out" else graph.in_edges(node, data=True)
    key_idx = 1 if direction == "out" else 0
    node_year = graph.nodes[node]["move"].year
    return [e[key_idx] for e in edges if e[2].get("kind") == "lineage" and graph.nodes[e[key_idx]]["move"].year != node_year]


def _tree_layout(graph: nx.DiGraph, year_y: dict[int, float]) -> dict[str, tuple[float, float]]:
    """Assign (x, y) positions: y from the node's year, x from chain structure.

    x-position is derived *only* from cross-year kind="lineage" edges (see
    _cross_year_lineage): those form simple, non-branching chains, so a
    node's x is always either a freshly-allocated slot (chain start) or
    exactly its single child's x (propagated backward up the chain).

    kind="trade" edges are deliberately excluded from this: an N-for-M trade
    wires every out-node to every in-node, so with M > 1 several trade-in
    nodes would share the identical predecessor set and derive the identical
    x - stacking their boxes exactly on top of each other. Trade partners are
    instead clustered by transaction below, and the trade lines themselves
    are still drawn afterwards in build_chart() using whatever positions
    result here - short because same-tx nodes land in adjacent slots, but
    correct (no two distinct nodes ever share a position) regardless.
    """
    positions: dict[str, tuple[float, float]] = {}
    next_slot = [0.0]

    def y_of(move: PlayerMove) -> float:
        return year_y[move.year] + SUB_Y.get(move.move_type, 0.0)

    def place_chain(node: str) -> float:
        if node in positions:
            return positions[node][0]
        children = _cross_year_lineage(graph, node, "out")
        if children:
            x = place_chain(children[0])  # at most one, by construction
        else:
            x = next_slot[0]
            next_slot[0] += 1.0
        positions[node] = (x, y_of(graph.nodes[node]["move"]))
        return x

    chain_starts = [n for n in graph.nodes if not _cross_year_lineage(graph, n, "in")]
    for node in chain_starts:
        if _cross_year_lineage(graph, node, "out"):
            place_chain(node)

    # Everything else (trade-only nodes and standalone adds/drops) is
    # clustered by transaction so trade partners land in adjacent slots,
    # grouped left-to-right by year for a chronological flow.
    remaining_by_year: dict[int, list[str]] = defaultdict(list)
    for n in graph.nodes:
        if n not in positions:
            remaining_by_year[graph.nodes[n]["move"].year].append(n)

    for year in sorted(remaining_by_year):
        nodes = remaining_by_year[year]
        nodes.sort(key=lambda n: (graph.nodes[n]["move"].tx_id, graph.nodes[n]["move"].date))
        for n in nodes:
            positions[n] = (next_slot[0], y_of(graph.nodes[n]["move"]))
            next_slot[0] += 1.0

    return {k: (NODE_START_X + x * H_GAP, y) for k, (x, y) in positions.items()}


def _draw_year_label(ax, x: float, y: float, year: int, color: str) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x - YEAR_BOX_W / 2, y - YEAR_BOX_H / 2),
            YEAR_BOX_W,
            YEAR_BOX_H,
            boxstyle="round,pad=0.05",
            linewidth=1.2,
            edgecolor="#888888",
            facecolor=color,
            zorder=3,
        )
    )
    ax.text(x, y, str(year), ha="center", va="center", fontsize=8, fontweight="bold", zorder=4)


def _draw_node(ax, x: float, y: float, move: PlayerMove) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x - NODE_W / 2, y - NODE_H / 2),
            NODE_W,
            NODE_H,
            boxstyle="round,pad=0.04",
            linewidth=0.9,
            edgecolor=TYPE_EDGE_COLOR[move.move_type],
            facecolor=TYPE_FACE_COLOR[move.move_type],
            zorder=3,
        )
    )
    ax.text(x, y, _wrap_name(move.player_name), ha="center", va="center", fontsize=5.5, zorder=4)


def _draw_edge(ax, pos_parent: tuple[float, float], pos_child: tuple[float, float]) -> None:
    x1, y1 = pos_parent
    x2, y2 = pos_child
    y_start = y1 - NODE_H
    y_end = y2 + NODE_H
    mid_y = (y_start + y_end) / 2.0
    ax.plot(
        [x1, x1, x2, x2],
        [y_start, mid_y, mid_y, y_end],
        color=CONNECTOR_COLOR,
        linewidth=CONNECTOR_LW,
        solid_capstyle="round",
        zorder=1,
    )


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
        output_path: PNG file path to save to, or None to skip saving.
        show: If True, open an interactive window after rendering.
        dpi: Resolution of the saved image.
    """
    if not moves_by_year:
        print("No transaction data to plot.")
        return

    years = sorted(moves_by_year)
    trade_pairs = build_trade_pairs(moves_by_year)
    year_y = {year: -idx * V_GAP for idx, year in enumerate(years)}

    graph = _build_graph(moves_by_year, trade_pairs)
    positions = _tree_layout(graph, year_y)

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    fig_w = max(20, (max(xs) - NODE_START_X) + 4) if xs else 20
    fig_h = max(8, len(years) * V_GAP + 2)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{team_name} — Transaction History", fontsize=13, fontweight="bold", pad=14)

    x_min = YEAR_LABEL_X - YEAR_BOX_W
    x_max = (max(xs) + NODE_W) if xs else 20
    half_band = V_GAP / 2 + max(abs(v) for v in SUB_Y.values()) + NODE_H + 0.05
    for idx, year in enumerate(years):
        y_center = year_y[year]
        color = YEAR_PALETTE[idx % len(YEAR_PALETTE)]
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (x_min, y_center - half_band),
                x_max - x_min,
                half_band * 2,
                boxstyle="square,pad=0",
                linewidth=0,
                facecolor=color,
                alpha=0.18,
                zorder=0,
            )
        )
        _draw_year_label(ax, YEAR_LABEL_X, y_center, year, color)

    for parent, child in graph.edges():
        if parent in positions and child in positions:
            _draw_edge(ax, positions[parent], positions[child])

    for node, (x, y) in positions.items():
        _draw_node(ax, x, y, graph.nodes[node]["move"])

    legend_items = [
        mpatches.Patch(facecolor=TYPE_FACE_COLOR[t], edgecolor=TYPE_EDGE_COLOR[t], label=label)
        for t, label in [(T_ADD, "Add"), (T_DROP, "Drop"), (T_TRADE_IN, "Trade In"), (T_TRADE_OUT, "Trade Out")]
    ]
    ax.legend(handles=legend_items, loc="upper right", fontsize=8, framealpha=0.9, title="Transaction Type", title_fontsize=8)

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
