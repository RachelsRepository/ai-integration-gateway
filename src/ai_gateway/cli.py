"""Command-line interface."""

from __future__ import annotations

import typer

app = typer.Typer(help="AI Integration Gateway administration CLI.", no_args_is_help=True)


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),  # noqa: S104
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Start the HTTP server."""
    import uvicorn

    uvicorn.run(
        "ai_gateway.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=True,
    )


@app.command("openapi")
def openapi(output: str = typer.Option("openapi.json", help="Output path.")) -> None:
    """Export the OpenAPI document."""
    import json
    from pathlib import Path

    from ai_gateway.api.app import create_app

    document = create_app().openapi()
    Path(output).write_text(json.dumps(document, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {output}")


@app.command("version")
def version() -> None:
    """Print the package version."""
    from ai_gateway import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
