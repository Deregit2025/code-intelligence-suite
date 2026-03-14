"""
CLI entry point for the Brownfield Cartographer.

Commands:
  analyze    Run the full (or incremental) analysis pipeline on a repo.
  query      Launch the interactive Navigator query interface.
  visualize  Render interactive HTML graphs (module imports + data lineage).

Examples:
  cartographer analyze https://github.com/dbt-labs/jaffle_shop
  cartographer analyze /path/to/local/repo --static-only
  cartographer analyze /path/to/repo --incremental
  cartographer query /path/to/repo "What produces the orders table?"
  cartographer query /path/to/repo  # interactive REPL
  cartographer visualize /path/to/repo
  cartographer visualize /path/to/repo --graph lineage --open
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Prompt

app = typer.Typer(
    name="cartographer",
    help="Brownfield Cartographer – Codebase Intelligence System",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="GitHub URL or local path to the repository"),
    incremental: bool = typer.Option(
        False, "--incremental", "-i", help="Re-analyse only files changed since last run"
    ),
    static_only: bool = typer.Option(
        False, "--static-only", help="Skip LLM calls (pure static analysis mode)"
    ),
    clone_dir: Path = typer.Option(
        Path("/tmp/cartographer_repos"),
        "--clone-dir",
        help="Directory for cloning remote repos",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Override the .cartography output directory name"
    ),
) -> None:
    """
    Run the full analysis pipeline on a repository.

    Produces .cartography/CODEBASE.md, onboarding_brief.md,
    module_graph.json, lineage_graph.json, and cartography_trace.jsonl.
    """
    from src.config import CONFIG
    from src.orchestrator import Orchestrator

    if output_dir:
        CONFIG.output_dir_name = output_dir

    try:
        orch = Orchestrator(
            repo_path=repo,
            clone_base=clone_dir,
            incremental=incremental,
            static_only=static_only,
        )
        orch.run()
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Analysis interrupted.[/yellow]")
        raise typer.Exit(130)


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------


@app.command()
def query(
    repo: str = typer.Argument(..., help="Local path to the already-analysed repository"),
    question: Optional[str] = typer.Argument(
        None, help="Question to ask (omit for interactive REPL)"
    ),
    use_langgraph: bool = typer.Option(
        False, "--langgraph", help="Use full LangGraph ReAct agent with local Ollama (no API key required)"
    ),
) -> None:
    """
    Query the cartography knowledge graph.

    Run without a question argument to enter interactive REPL mode.
    """
    from src.agents.navigator import Navigator
    from src.config import CONFIG

    repo_path = Path(repo).resolve()
    cartography_dir = repo_path / CONFIG.output_dir_name

    if not cartography_dir.exists():
        console.print(
            f"[red]No cartography artifacts found at {cartography_dir}.[/red]\n"
            "Run [bold]cartographer analyze[/bold] first."
        )
        raise typer.Exit(1)

    try:
        navigator = Navigator(cartography_dir)
    except Exception as exc:
        console.print(f"[red]Failed to load knowledge graph:[/red] {exc}")
        raise typer.Exit(1)

    def _run_query(q: str) -> None:
        if use_langgraph:
            answer = navigator.run_langgraph_agent(q)
        else:
            answer = navigator.query(q)
        console.print(f"\n{answer}\n")

    if question:
        # Single query mode
        _run_query(question)
    else:
        # Interactive REPL
        console.print(
            "[bold cyan]Cartographer Navigator[/bold cyan] – type a question or [bold]exit[/bold]"
        )
        console.print(f"Repository: [yellow]{repo_path.name}[/yellow]")
        console.print("")
        console.print("Examples:")
        console.print('  "What produces the orders table?"')
        console.print('  "Blast radius of src/transforms/revenue.py"')
        console.print('  "Where is the revenue calculation logic?"')
        console.print('  "Explain src/ingestion/kafka_consumer.py"')
        console.print("")

        while True:
            try:
                user_input = Prompt.ask("[bold green]>[/bold green]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            if user_input.strip().lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye.[/dim]")
                break

            if not user_input.strip():
                continue

            _run_query(user_input.strip())


# ---------------------------------------------------------------------------
# visualize command
# ---------------------------------------------------------------------------


@app.command()
def visualize(
    repo: str = typer.Argument(..., help="Local path to the already-analysed repository"),
    graph: str = typer.Option(
        "both",
        "--graph",
        "-g",
        help="Which graph to render: 'module', 'lineage', or 'both'",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for HTML files (defaults to .cartography/)",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open generated HTML files in the default browser automatically",
    ),
) -> None:
    """
    Render interactive graph visualizations as standalone HTML files.

    Features:
      - Zoom / pan / drag nodes
      - Click a node to highlight its direct neighbors
      - Hover tooltips with purpose statements, metrics, lineage info
      - Dark-mode UI with physics simulation (Barnes-Hut / Repulsion)

    Generated files can be opened directly in any browser (no server needed).
    """
    from src.config import CONFIG
    from src.utils.visualizer import Visualizer

    repo_path = Path(repo).resolve()
    cartography_dir = repo_path / CONFIG.output_dir_name

    if not cartography_dir.exists():
        console.print(
            f"[red]No cartography artifacts found at {cartography_dir}.[/red]\n"
            "Run [bold]cartographer analyze[/bold] first."
        )
        raise typer.Exit(1)

    try:
        from pyvis.network import Network  # noqa: F401
    except ImportError:
        console.print(
            "[red]pyvis is not installed.[/red] "
            "Run: [bold]poetry run pip install pyvis[/bold]"
        )
        raise typer.Exit(1)

    out_dir = output or cartography_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_choice = graph.lower().strip()
    if graph_choice not in ("module", "lineage", "both"):
        console.print(
            f"[red]Unknown --graph value '{graph}'. Use: module, lineage, or both.[/red]"
        )
        raise typer.Exit(1)

    console.print(
        f"[bold cyan]Cartographer Visualizer[/bold cyan] – "
        f"rendering '{graph_choice}' graph(s)"
    )
    console.print(f"Output → [yellow]{out_dir}[/yellow]\n")

    from src.graph.graph_serializers import load_knowledge_graph

    kg = load_knowledge_graph(cartography_dir)
    v = Visualizer(kg)
    generated: list[Path] = []

    if graph_choice in ("module", "both"):
        p = out_dir / "module_graph.html"
        with console.status("Rendering module import graph…"):
            v.render_module_graph(p)
        console.print(f"  \u2705 Module graph   \u2192 [green]{p}[/green]")
        generated.append(p)

    if graph_choice in ("lineage", "both"):
        p = out_dir / "lineage_graph.html"
        with console.status("Rendering data lineage graph…"):
            v.render_lineage_graph(p)
        console.print(f"  \u2705 Lineage graph  \u2192 [green]{p}[/green]")
        generated.append(p)

    console.print("\n[bold]Open in your browser:[/bold]")
    for p in generated:
        console.print(f"  file:///{p.resolve().as_posix()}")

    if open_browser:
        import webbrowser
        for p in generated:
            webbrowser.open(f"file:///{p.resolve().as_posix()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()