#!/usr/bin/env python3
"""
Launch the qlty MCP server using the OPC uv project environment.

Why this exists:
- MCP stdio servers are launched without a shell, so `$HOME` won't expand.
- The qlty server depends on Python packages (e.g., `mcp`) that are installed
  via `uv` in the OPC project (typically `.../opc/.venv`).

This wrapper:
1. Resolves the canonical `opc/` directory (where `pyproject.toml` lives) via:
   - CLAUDE_OPC_DIR env var
   - ~/.claude/opc_dir (written by the installer)
   - $CLAUDE_PROJECT_DIR/opc
   - $CWD/opc
2. `chdir`s into that directory and execs:
   `uv run python <this_dir>/server.py`
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _looks_like_opc_dir(path: Path) -> bool:
    try:
        return path.exists() and (path / "pyproject.toml").exists() and (path / "scripts").exists()
    except OSError:
        return False


def _read_persisted_opc_dir() -> Path | None:
    home = Path.home()
    opc_dir_file = home / ".claude" / "opc_dir"
    if not opc_dir_file.exists():
        return None
    try:
        raw = opc_dir_file.read_text(encoding="utf-8").strip()
        cleaned = raw.strip().strip("'").strip('"')
        if not cleaned:
            return None
        return Path(cleaned).expanduser().resolve()
    except OSError:
        return None


def resolve_opc_dir() -> Path | None:
    # 1) Explicit env override
    env_opc = os.environ.get("CLAUDE_OPC_DIR")
    if env_opc:
        candidate = Path(env_opc).expanduser().resolve()
        if _looks_like_opc_dir(candidate):
            return candidate

    # 2) Installer-persisted location (~/.claude/opc_dir)
    persisted = _read_persisted_opc_dir()
    if persisted and _looks_like_opc_dir(persisted):
        return persisted

    # 3) Project-relative
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        candidate = (Path(project_dir) / "opc").resolve()
        if _looks_like_opc_dir(candidate):
            return candidate

    # 4) CWD-relative
    candidate = (Path.cwd() / "opc").resolve()
    if _looks_like_opc_dir(candidate):
        return candidate

    return None


def main() -> None:
    server_path = Path(__file__).resolve().parent / "server.py"
    if not server_path.exists():
        print(f"ERROR: qlty server.py not found at {server_path}", file=sys.stderr)
        raise SystemExit(1)

    opc_dir = resolve_opc_dir()
    if not opc_dir:
        print(
            "ERROR: Could not resolve OPC project directory.\n"
            "- Set CLAUDE_OPC_DIR to your opc/ path, or\n"
            "- Ensure ~/.claude/opc_dir exists (written by the installer), or\n"
            "- Run from a repo that contains ./opc/ with pyproject.toml.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Ensure uv can discover the project by running from the opc/ directory.
    try:
        os.chdir(opc_dir)
    except OSError as e:
        print(f"ERROR: Failed to chdir to {opc_dir}: {e}", file=sys.stderr)
        raise SystemExit(1)

    # Replace this process with uv -> python -> MCP server, preserving stdio.
    try:
        os.execvp("uv", ["uv", "run", "python", str(server_path)])
    except FileNotFoundError:
        print("ERROR: `uv` not found on PATH. Install uv and try again.", file=sys.stderr)
        raise SystemExit(127)


if __name__ == "__main__":
    main()

