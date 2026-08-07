from __future__ import annotations

from pathlib import Path

import pandas as pd
import typer

from spy_constituent_alpha.config import load_settings

app = typer.Typer(no_args_is_help=True)


@app.command()
def validate_config(path: Path = typer.Argument(..., exists=True)) -> None:
    """Validate and normalize a YAML configuration file."""
    settings = load_settings(path)
    typer.echo(settings.model_dump_json(indent=2))


@app.command()
def summarize_edges(path: Path = typer.Argument(..., exists=True), top: int = 20) -> None:
    """Print the highest-ranked candidate edges from a CSV export."""
    frame = pd.read_csv(path)
    if "edge_to_uncertainty" in frame.columns:
        frame = frame.sort_values("edge_to_uncertainty", ascending=False)
    typer.echo(frame.head(top).to_string(index=False))


if __name__ == "__main__":
    app()
