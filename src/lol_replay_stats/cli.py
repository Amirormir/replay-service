"""Typer CLI: parse a .rofl file or launch the FastAPI server.

Usage examples:
    lol-stats parse /path/to/game.rofl                  # pretty-printed enriched JSON
    lol-stats parse /path/to/game.rofl --raw            # raw metadata dict
    lol-stats parse /path/to/game.rofl -o out.json      # write to file
    lol-stats serve --port 8000                         # launch FastAPI
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.json import JSON

from .parser import ReplayParseError, parse_rofl
from .stats import enrich

app = typer.Typer(
    help="Parse League of Legends .rofl replays and emit enriched scoreboard stats.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def parse(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    raw: bool = typer.Option(
        False, "--raw", help="Emit the raw metadata dict instead of the enriched payload."
    ),
    output: Path | None = typer.Option(
        None, "-o", "--output", help="Write JSON to this file instead of stdout."
    ),
) -> None:
    """Parse a .rofl file and print the resulting JSON."""
    try:
        raw_meta = parse_rofl(file)
    except ReplayParseError as e:
        console.print(f"[red]parse error:[/red] {e}")
        raise typer.Exit(code=1) from e

    if raw:
        payload: dict = raw_meta
    else:
        try:
            payload = enrich(raw_meta).model_dump(mode="json")
        except ValueError as e:
            console.print(f"[red]enrichment error:[/red] {e}")
            raise typer.Exit(code=1) from e

    serialised = json.dumps(payload, indent=2, ensure_ascii=False)

    if output is not None:
        output.write_text(serialised, encoding="utf-8")
        console.print(f"[green]wrote[/green] {output}")
    else:
        # rich.JSON re-parses then prints with syntax highlighting
        console.print(JSON(serialised))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="Port to listen on."),
    reload: bool = typer.Option(False, help="Enable autoreload (dev only)."),
) -> None:
    """Launch the FastAPI server (uvicorn)."""
    import uvicorn

    uvicorn.run(
        "lol_replay_stats.api:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
