"""Architecture boundary tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.architecture
def test_import_linter_contracts() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    lint_imports = ROOT / ".venv" / "bin" / "lint-imports"
    command = (
        [str(lint_imports)] if lint_imports.exists() else [sys.executable, "-m", "importlinter"]
    )
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.architecture
def test_domain_has_no_framework_imports() -> None:
    forbidden = ("fastapi", "sqlalchemy", "redis", "httpx", "pydantic", "aiokafka")
    domain = ROOT / "src" / "ai_gateway" / "domain"
    offenders: list[str] = []
    for path in domain.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if f"import {name}" in text or f"from {name}" in text:
                offenders.append(f"{path}:{name}")
    assert offenders == []
