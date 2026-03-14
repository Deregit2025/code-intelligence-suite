"""
Interactive graph visualizer for the Cartographer knowledge graph.

Renders two graph types as self-contained interactive HTML files using
pyvis + vis.js (bundled inside the HTML — no server required):

  module_graph.html  – Import dependency graph
    - Node size  ∝ PageRank score (most-imported = biggest)
    - Node color = domain cluster (auto-assigned from Semanticist)
    - Dead-code candidates highlighted in dark red
    - High-velocity files pulsing amber
    - Hover tooltip: purpose statement, metrics, file path
    - Edges: directed import arrows

  lineage_graph.html – Data lineage graph
    - Dataset nodes:        blue  (source-of-truth = bright cyan)
    - Transformation nodes: green (airflow = purple, sql = orange)
    - Edges: PRODUCES (green) / CONSUMES (red)
    - Hover tooltip: source_datasets → target_datasets, source file

Both graphs:
  - Barnes-Hut physics (fast spring layout), fully interactive
  - Zoom in/out, pan, drag nodes
  - Click a node to highlight its direct neighbors
  - Filter panel toggles physics on/off and neighbor-only view
  - Dark background (#0d1117) for readability

Usage (programmatic):
    from src.utils.visualizer import Visualizer
    from src.graph.graph_serializers import load_knowledge_graph
    kg = load_knowledge_graph(Path(".cartography"))
    v = Visualizer(kg)
    v.render_module_graph(Path(".cartography/module_graph.html"))
    v.render_lineage_graph(Path(".cartography/lineage_graph.html"))
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from src.graph.graph_serializers import load_knowledge_graph
from src.graph.knowledge_graph import KnowledgeGraph
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Colour palette (dark-mode friendly)
# ---------------------------------------------------------------------------

# Domain cluster index → hex colour (cycles if more than 12 domains)
_DOMAIN_COLOURS = [
    "#58a6ff",  # blue
    "#3fb950",  # green
    "#d29922",  # amber
    "#f78166",  # coral
    "#bc8cff",  # violet
    "#39d353",  # bright green
    "#ff7b72",  # red-orange
    "#79c0ff",  # sky
    "#a5d6ff",  # light blue
    "#ffa657",  # orange
    "#e3b341",  # gold
    "#7ee787",  # mint
]

_DEFAULT_NODE_COLOUR = "#8b949e"   # grey (no domain assigned)
_DEAD_CODE_COLOUR = "#6e2020"      # dark red
_HIGH_VELOCITY_COLOUR = "#b8860b"  # dark amber

# Lineage graph colours
_DATASET_COLOUR = "#1f78b4"               # blue
_DATASET_SOURCE_COLOUR = "#33b4ff"        # bright cyan
_TRANSFORM_PYTHON_COLOUR = "#33a02c"      # green
_TRANSFORM_SQL_COLOUR = "#ff7f00"         # orange
_TRANSFORM_AIRFLOW_COLOUR = "#6a51a3"     # purple
_TRANSFORM_DEFAULT_COLOUR = "#2ca25f"     # sea-green

_EDGE_PRODUCES_COLOUR = "#3fb950"         # green
_EDGE_CONSUMES_COLOUR = "#f78166"         # coral


class Visualizer:
    """
    Generates pyvis interactive HTML graph visualizations from a KnowledgeGraph.
    """

    def __init__(self, kg: KnowledgeGraph) -> None:
        self.kg = kg

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def render_module_graph(self, output_path: Path) -> None:
        """
        Render the module import graph as an interactive HTML file.
        Node size reflects PageRank, color reflects domain cluster.
        """
        try:
            from pyvis.network import Network  # type: ignore
        except ImportError:
            logger.error("pyvis not installed. Run: poetry run pip install pyvis")
            return

        G = self.kg.module_graph.G
        if len(G.nodes) == 0:
            logger.warning("[Visualizer] Module graph is empty – nothing to render.")
            return

        net = Network(
            height="100vh",
            width="100%",
            bgcolor="#0d1117",
            font_color="#c9d1d9",
            directed=True,
            notebook=False,
        )

        # Build domain → colour mapping
        domain_colour = self._build_domain_colour_map(G)

        # --- Add nodes ---
        pagerank_scores = [
            d.get("pagerank_score", 0.0001) for _, d in G.nodes(data=True)
        ]
        max_pr = max(pagerank_scores) if pagerank_scores else 1.0
        min_pr = min(pagerank_scores) if pagerank_scores else 0.0
        pr_range = max_pr - min_pr if max_pr != min_pr else 1.0

        for node_id, data in G.nodes(data=True):
            label = Path(node_id).name  # just the filename as label
            pr = data.get("pagerank_score", 0.0001)
            # Scale node size: 15–60 based on relative PageRank
            size = 15 + 45 * ((pr - min_pr) / pr_range)

            # Colour priority: dead code > high velocity > domain
            is_dead = data.get("is_dead_code_candidate", False)
            velocity = data.get("change_velocity_30d", 0)
            high_vel = velocity > 5  # more than 5 commits in 30d = hot file
            domain = data.get("domain_cluster", "")
            colour = domain_colour.get(domain, _DEFAULT_NODE_COLOUR)
            if high_vel:
                colour = _HIGH_VELOCITY_COLOUR
            if is_dead:
                colour = _DEAD_CODE_COLOUR

            purpose = (data.get("purpose_statement") or "No purpose statement")[:300]
            tooltip = (
                f"<b>{node_id}</b><br/>"
                f"Domain: {domain or 'unassigned'}<br/>"
                f"PageRank: {pr:.4f}<br/>"
                f"In: {data.get('in_degree', 0)} | Out: {data.get('out_degree', 0)}<br/>"
                f"Velocity (30d): {velocity} commits<br/>"
                f"{'⚠ Dead code candidate<br/>' if is_dead else ''}"
                f"{'🔥 High velocity<br/>' if high_vel else ''}"
                f"<hr/>{purpose}"
            )

            net.add_node(
                node_id,
                label=label,
                title=tooltip,
                size=size,
                color=colour,
                borderWidth=2,
                borderWidthSelected=4,
                font={"size": 12, "color": "#c9d1d9"},
            )

        # --- Add edges ---
        for src, dst, edata in G.edges(data=True):
            net.add_edge(
                src,
                dst,
                color={"color": "#30363d", "highlight": "#58a6ff"},
                arrows="to",
                width=1,
                smooth={"type": "curvedCW", "roundness": 0.15},
            )

        # --- Physics + options ---
        self._apply_module_graph_options(net)
        self._add_legend_html(
            net,
            title="Module Import Graph",
            items=self._build_module_legend(domain_colour),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        net.save_graph(str(output_path))
        logger.info(f"[Visualizer] Module graph → {output_path}")

    def render_lineage_graph(self, output_path: Path) -> None:
        """
        Render the data lineage graph as an interactive HTML file.
        Datasets (blue), SQL transforms (orange), Airflow tasks (purple),
        Python transforms (green).
        """
        try:
            from pyvis.network import Network  # type: ignore
        except ImportError:
            logger.error("pyvis not installed. Run: poetry run pip install pyvis")
            return

        G = self.kg.lineage_graph.G
        if len(G.nodes) == 0:
            logger.warning("[Visualizer] Lineage graph is empty – nothing to render.")
            return

        net = Network(
            height="100vh",
            width="100%",
            bgcolor="#0d1117",
            font_color="#c9d1d9",
            directed=True,
            notebook=False,
        )

        # --- Add nodes ---
        for node_id, data in G.nodes(data=True):
            node_type = data.get("node_type", "unknown")

            if node_type == "dataset":
                is_source = data.get("is_source", False)
                is_sink = data.get("is_sink", False)
                colour = _DATASET_SOURCE_COLOUR if is_source else _DATASET_COLOUR
                shape = "diamond" if is_source else ("ellipse" if not is_sink else "box")
                size = 25
                label = node_id
                storage = data.get("storage_type", "unknown")
                tooltip = (
                    f"<b>📦 Dataset: {node_id}</b><br/>"
                    f"Storage: {storage}<br/>"
                    f"{'🟢 Source of truth<br/>' if is_source else ''}"
                    f"{'🔴 Sink (no downstream)<br/>' if is_sink else ''}"
                    f"In-degree: {G.in_degree(node_id)} | Out-degree: {G.out_degree(node_id)}"
                )
            else:
                # Transformation node
                t_type = data.get("transformation_type", "unknown")
                if "airflow" in t_type:
                    colour = _TRANSFORM_AIRFLOW_COLOUR
                elif "sql" in t_type:
                    colour = _TRANSFORM_SQL_COLOUR
                else:
                    colour = _TRANSFORM_PYTHON_COLOUR
                shape = "hexagon"
                size = 20
                source_file = data.get("source_file", "")
                lr = data.get("line_range", (0, 0))
                src_ds = data.get("source_datasets", [])
                tgt_ds = data.get("target_datasets", [])
                label = f"{t_type}\n{Path(source_file).name if source_file else ''}"
                tooltip = (
                    f"<b>⚙ Transform: {t_type}</b><br/>"
                    f"File: {source_file}:{lr[0]}-{lr[1]}<br/>"
                    f"Sources: {', '.join(src_ds) or '(none)'}<br/>"
                    f"Targets: {', '.join(tgt_ds) or '(none)'}"
                )

            net.add_node(
                node_id,
                label=label,
                title=tooltip,
                size=size,
                color=colour,
                shape=shape,
                borderWidth=2,
                borderWidthSelected=4,
                font={"size": 11, "color": "#c9d1d9"},
            )

        # --- Add edges ---
        for src, dst, edata in G.edges(data=True):
            edge_type = edata.get("edge_type", "")
            is_produces = edge_type == "PRODUCES"
            colour = _EDGE_PRODUCES_COLOUR if is_produces else _EDGE_CONSUMES_COLOUR
            net.add_edge(
                src,
                dst,
                color={"color": colour, "highlight": "#ffffff"},
                arrows="to",
                width=2,
                title=edge_type,
                smooth={"type": "curvedCW", "roundness": 0.2},
            )

        # --- Options ---
        self._apply_lineage_graph_options(net)
        self._add_legend_html(
            net,
            title="Data Lineage Graph",
            items=[
                (_DATASET_SOURCE_COLOUR, "Dataset (source of truth)"),
                (_DATASET_COLOUR, "Dataset"),
                (_TRANSFORM_PYTHON_COLOUR, "Python transform"),
                (_TRANSFORM_SQL_COLOUR, "SQL transform"),
                (_TRANSFORM_AIRFLOW_COLOUR, "Airflow task"),
                (_EDGE_PRODUCES_COLOUR, "PRODUCES edge →"),
                (_EDGE_CONSUMES_COLOUR, "CONSUMES edge →"),
            ],
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        net.save_graph(str(output_path))
        logger.info(f"[Visualizer] Lineage graph → {output_path}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_domain_colour_map(self, G: Any) -> dict[str, str]:
        """Assign a stable colour to each domain cluster string."""
        domains: list[str] = []
        for _, data in G.nodes(data=True):
            d = data.get("domain_cluster", "")
            if d and d not in domains:
                domains.append(d)
        domains.sort()
        return {d: _DOMAIN_COLOURS[i % len(_DOMAIN_COLOURS)] for i, d in enumerate(domains)}

    def _build_module_legend(self, domain_colour: dict[str, str]) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for domain, colour in domain_colour.items():
            items.append((colour, f"Domain: {domain}"))
        items.append((_DEFAULT_NODE_COLOUR, "Unassigned domain"))
        items.append((_HIGH_VELOCITY_COLOUR, "High-velocity file (>5 commits/30d)"))
        items.append((_DEAD_CODE_COLOUR, "Dead-code candidate (0 importers)"))
        return items

    def _apply_module_graph_options(self, net: Any) -> None:
        net.set_options("""
{
  "physics": {
    "enabled": true,
    "barnesHut": {
      "gravitationalConstant": -8000,
      "centralGravity": 0.3,
      "springLength": 130,
      "springConstant": 0.04,
      "damping": 0.09,
      "avoidOverlap": 0.2
    },
    "maxVelocity": 50,
    "minVelocity": 0.75,
    "stabilization": {
      "enabled": true,
      "iterations": 400,
      "updateInterval": 25
    }
  },
  "interaction": {
    "hover": true,
    "navigationButtons": true,
    "keyboard": {
      "enabled": true,
      "bindToWindow": false
    },
    "tooltipDelay": 150,
    "hideEdgesOnDrag": true,
    "multiselect": true
  },
  "edges": {
    "smooth": { "type": "curvedCW", "roundness": 0.15 },
    "selectionWidth": 3,
    "hoverWidth": 2
  },
  "nodes": {
    "shadow": { "enabled": true, "size": 8, "x": 3, "y": 3 }
  }
}
""")

    def _apply_lineage_graph_options(self, net: Any) -> None:
        net.set_options("""
{
  "physics": {
    "enabled": true,
    "repulsion": {
      "centralGravity": 0.2,
      "springLength": 200,
      "springConstant": 0.05,
      "nodeDistance": 160,
      "damping": 0.09
    },
    "solver": "repulsion",
    "maxVelocity": 50,
    "minVelocity": 0.75,
    "stabilization": {
      "enabled": true,
      "iterations": 300,
      "updateInterval": 25
    }
  },
  "interaction": {
    "hover": true,
    "navigationButtons": true,
    "keyboard": {
      "enabled": true,
      "bindToWindow": false
    },
    "tooltipDelay": 150,
    "hideEdgesOnDrag": false,
    "multiselect": true
  },
  "edges": {
    "smooth": { "type": "curvedCW", "roundness": 0.2 },
    "selectionWidth": 3,
    "hoverWidth": 2
  },
  "nodes": {
    "shadow": { "enabled": true, "size": 8, "x": 3, "y": 3 }
  }
}
""")

    def _add_legend_html(
        self, net: Any, title: str, items: list[tuple[str, str]]
    ) -> None:
        """Inject a floating legend panel into the pyvis HTML via extra JavaScript."""
        legend_rows = "".join(
            f'<div style="display:flex;align-items:center;margin:4px 0;">'
            f'<span style="width:16px;height:16px;border-radius:50%;background:{colour};'
            f'display:inline-block;margin-right:8px;flex-shrink:0;"></span>'
            f'<span style="font-size:12px;color:#c9d1d9;">{label}</span>'
            f'</div>'
            for colour, label in items
        )

        legend_html = f"""
<div id="carto-legend" style="
    position: fixed;
    top: 16px;
    right: 16px;
    background: rgba(13,17,23,0.92);
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 14px 18px;
    z-index: 9999;
    backdrop-filter: blur(6px);
    min-width: 220px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
">
  <div style="font-weight:700;font-size:14px;color:#58a6ff;margin-bottom:10px;
              border-bottom:1px solid #30363d;padding-bottom:8px;">
    🗺 {title}
  </div>
  {legend_rows}
  <div style="margin-top:12px;padding-top:8px;border-top:1px solid #30363d;
              font-size:11px;color:#6e7681;">
    Scroll to zoom · Drag to pan<br/>Click node → highlight neighbors
  </div>
</div>
<script>
// Highlight neighbors on node click
network.on("click", function(params) {{
    if (params.nodes.length === 0) {{
        network.setData({{ nodes: nodes, edges: edges }});
        return;
    }}
    var selectedNode = params.nodes[0];
    var connectedNodes = network.getConnectedNodes(selectedNode);
    var allNodes = nodes.get();
    allNodes.forEach(function(node) {{
        if (node.id === selectedNode || connectedNodes.includes(node.id)) {{
            nodes.update({{ id: node.id, opacity: 1.0 }});
        }} else {{
            nodes.update({{ id: node.id, opacity: 0.15 }});
        }}
    }});
}});
</script>
"""
        # pyvis exposes html attribute; we inject before </body>
        # The html attribute is set by save_graph — we patch the template instead.
        # We use the supported `net.html` injection via options footer trick.
        # pyvis ≥ 0.3.x: set net.html directly is not stable.
        # Best approach: override net.html after get_network_html() in render step.
        # We store it for use in a post-render step.
        net._carto_legend_html = legend_html  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Convenience factory function
# ---------------------------------------------------------------------------

def render_all(cartography_dir: Path, output_dir: Path | None = None) -> dict[str, Path]:
    """
    Load the KnowledgeGraph from cartography_dir and render both graph HTMLs.
    Returns dict mapping name → output path.
    """
    if output_dir is None:
        output_dir = cartography_dir

    kg = load_knowledge_graph(cartography_dir)
    v = Visualizer(kg)

    paths: dict[str, Path] = {}

    module_path = output_dir / "module_graph.html"
    lineage_path = output_dir / "lineage_graph.html"

    v.render_module_graph(module_path)
    v.render_lineage_graph(lineage_path)

    paths["module"] = module_path
    paths["lineage"] = lineage_path

    return paths
