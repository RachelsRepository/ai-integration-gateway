"""Architecture boundary tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _lint_imports_command() -> list[str]:
    """Resolve the official Import Linter CLI entrypoint.

    Import Linter documents ``lint-imports`` (and the alias ``import-linter lint``) as the
    supported invocation. It does not support ``python -m importlinter``.

    Resolution order:
    1. ``lint-imports`` on ``PATH`` (activated venv, CI, uv/pip tool shims).
    2. Console script beside ``sys.executable`` (same environment as the test runner when
       the scripts directory is not on ``PATH``; the path is not fully resolved so venv
       interpreter symlinks still point at the environment's ``bin``/``Scripts``).
    3. Documented ``import-linter lint`` alias via the same search strategy.

    Returns:
        An argv list ready for ``subprocess.run``.

    Raises:
        pytest.fail: If the CLI cannot be located in the active environment.
    """
    which = shutil.which("lint-imports")
    if which is not None:
        return [which]

    scripts_dir = Path(sys.executable).parent
    for name in ("lint-imports", "lint-imports.exe"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return [str(candidate)]

    alias = shutil.which("import-linter")
    if alias is not None:
        return [alias, "lint"]

    for name in ("import-linter", "import-linter.exe"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return [str(candidate), "lint"]

    pytest.fail(
        "Import Linter CLI was not found. Install the project's [dev] extras so the "
        "official `lint-imports` console script is available on PATH (or next to "
        f"{sys.executable})."
    )


@pytest.mark.architecture
def test_import_linter_contracts() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        _lint_imports_command(),
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
