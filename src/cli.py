"""
CLI entry point for the Brownfield Cartographer.

Commands:
  analyze   Run the full (or incremental) analysis pipeline on a repo.
  query     Launch the interactive Navigator query interface.

Examples:
  cartographer analyze https://github.com/dbt-labs/jaffle_shop
  cartographer analyze /path/to/local/repo --static-only
  cartographer analyze /path/to/repo --incremental
  cartographer query /path/to/repo "What produces the orders table?"
  cartographer query /path/to/repo  # interactive REPL
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
        False, "--langgraph", help="Use full LangGraph ReAct agent (requires LLM key)"
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()