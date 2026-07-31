"""CLI tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ai_gateway.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.stdout


def test_cli_openapi_writes_file(tmp_path: Path) -> None:
    output = tmp_path / "openapi.json"
    result = runner.invoke(app, ["openapi", "--output", str(output)])
    assert result.exit_code == 0
    assert output.exists()
    assert "openapi" in output.read_text(encoding="utf-8")


def test_cli_serve_invokes_uvicorn() -> None:
    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(app, ["serve", "--port", "9000"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
