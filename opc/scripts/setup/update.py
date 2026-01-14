#!/usr/bin/env python3
"""OPC Update Wizard - Pull latest and update installed components.

Updates hooks, skills, rules, and agents from the latest OPC repo.
Preserves user customizations by only updating OPC-owned files.

USAGE:
    uv run python -m scripts.setup.update
    # or
    python scripts/setup/update.py
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure project root is in sys.path
_this_file = Path(__file__).resolve()
_project_root = _this_file.parent.parent.parent  # scripts/setup/update.py -> opc/
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from rich.console import Console
    from rich.prompt import Confirm
    console = Console()
except ImportError:
    class _FallbackConsole:
        def print(self, *args, **kwargs):
            # Strip rich markup for plain output
            text = args[0] if args else ""
            import re
            text = re.sub(r'\[.*?\]', '', str(text))
            print(text)
    console = _FallbackConsole()
    Confirm = None  # type: ignore


def file_hash(path: Path) -> str:
    """Get MD5 hash of file contents."""
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def get_opc_dir() -> Path:
    """Get OPC directory (where this script lives)."""
    return Path(__file__).resolve().parent.parent.parent


def get_global_claude_dir() -> Path:
    """Get global ~/.claude directory."""
    return Path.home() / ".claude"


def git_pull(repo_dir: Path) -> tuple[bool, str]:
    """Pull latest from git remote.

    Returns:
        Tuple of (success, message)
    """
    try:
        # Check if we're in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, "Not a git repository"

        # Get current commit
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        ).stdout.strip()[:8]

        # Pull latest
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            # Try to provide helpful error
            if "diverged" in result.stderr.lower():
                return False, "Local changes conflict with remote. Commit or stash first."
            return False, f"Git pull failed: {result.stderr[:200]}"

        # Get new commit
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        ).stdout.strip()[:8]

        if before == after:
            return True, "Already up to date"
        else:
            return True, f"Updated {before} → {after}"

    except subprocess.TimeoutExpired:
        return False, "Git pull timed out"
    except FileNotFoundError:
        return False, "Git not found"
    except Exception as e:
        return False, str(e)


def compare_directories(source: Path, installed: Path, extensions: set[str] | None = None) -> dict:
    """Compare source and installed directories.

    Args:
        source: OPC source directory
        installed: User's installed directory
        extensions: File extensions to check (e.g., {'.ts', '.py', '.md'})

    Returns:
        Dict with 'new', 'updated', 'unchanged' lists of relative paths
    """
    result = {"new": [], "updated": [], "unchanged": []}

    if not source.exists():
        return result

    # Get all source files
    for src_file in source.rglob("*"):
        if src_file.is_dir():
            continue
        if extensions and src_file.suffix not in extensions:
            continue

        # Get path relative to source for filtering
        rel_path = src_file.relative_to(source)

        # Skip node_modules, dist, __pycache__, hidden dirs, etc.
        if any(part.startswith(('.', '__')) or part == 'node_modules' or part == 'dist'
               for part in rel_path.parts):
            continue

        inst_file = installed / rel_path

        if not inst_file.exists():
            result["new"].append(str(rel_path))
        elif file_hash(src_file) != file_hash(inst_file):
            result["updated"].append(str(rel_path))
        else:
            result["unchanged"].append(str(rel_path))

    return result


def copy_file(src: Path, dst: Path) -> bool:
    """Copy file, creating parent directories as needed."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def build_typescript_hooks(hooks_dir: Path) -> tuple[bool, str]:
    """Build TypeScript hooks using npm."""
    if not (hooks_dir / "package.json").exists():
        return True, "No package.json"

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        return False, "npm not found"

    try:
        # Install deps
        result = subprocess.run(
            [npm_cmd, "install"],
            cwd=hooks_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return False, f"npm install failed: {result.stderr[:100]}"

        # Build
        result = subprocess.run(
            [npm_cmd, "run", "build"],
            cwd=hooks_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, f"npm build failed: {result.stderr[:100]}"

        return True, "Built successfully"
    except subprocess.TimeoutExpired:
        return False, "Timed out"
    except Exception as e:
        return False, str(e)


def run_update() -> None:
    """Run the update wizard."""
    console.print("[bold]OPC UPDATE WIZARD[/bold]\n")

    opc_dir = get_opc_dir()
    claude_dir = get_global_claude_dir()

    # Check if installed
    if not claude_dir.exists():
        console.print("[red]No ~/.claude found. Run the install wizard first:[/red]")
        console.print("  uv run python -m scripts.setup.wizard")
        sys.exit(1)

    # Step 1: Git pull
    console.print("[bold]Step 1/6: Pulling latest from GitHub...[/bold]")
    repo_root = opc_dir.parent  # Go up from opc/ to repo root
    success, msg = git_pull(repo_root)
    if success:
        console.print(f"  [green]OK[/green] {msg}")
    else:
        console.print(f"  [yellow]WARN[/yellow] {msg}")
        if Confirm and not Confirm.ask("Continue anyway?", default=False):
            sys.exit(1)

    # Step 2: Compare files
    console.print("\n[bold]Step 2/6: Comparing installed files...[/bold]")

    # Source directories are in the repo's .claude/ integration folder
    integration_source = opc_dir.parent / ".claude"

    # Define what to check: (source_subdir, installed_path, extensions)
    checks = [
        ("hooks/src", claude_dir / "hooks" / "src", {".ts"}),
        ("skills", claude_dir / "skills", {".md"}),
        ("rules", claude_dir / "rules", {".md"}),
        ("agents", claude_dir / "agents", {".md", ".yaml", ".yml"}),
        ("scripts", claude_dir / "scripts", {".py", ".sh"}),
        ("scripts/core", claude_dir / "scripts" / "core", {".py"}),
    ]

    all_new = []
    all_updated = []
    ts_updated = False

    for subdir, installed_path, extensions in checks:
        source_path = integration_source / subdir
        diff = compare_directories(source_path, installed_path, extensions)

        for f in diff["new"]:
            all_new.append((subdir, f, source_path, installed_path))
        for f in diff["updated"]:
            all_updated.append((subdir, f, source_path, installed_path))
            if f.endswith(".ts"):
                ts_updated = True

        # Show status
        status = []
        if diff["new"]:
            status.append(f"{len(diff['new'])} new")
        if diff["updated"]:
            status.append(f"{len(diff['updated'])} updated")
        if diff["unchanged"]:
            status.append(f"{len(diff['unchanged'])} unchanged")

        if status:
            console.print(f"  {subdir}: {', '.join(status)}")
        else:
            console.print(f"  {subdir}: [dim]not found in source[/dim]")

    # Step 3: Apply updates
    console.print("\n[bold]Step 3/6: Applying updates...[/bold]")

    if not all_new and not all_updated:
        console.print("  [green]Everything is up to date![/green]")
    else:
        # Show what will be updated
        if all_new:
            console.print(f"  New files to install: {len(all_new)}")
            for subdir, f, _, _ in all_new[:5]:
                console.print(f"    [green]+[/green] {subdir}/{f}")
            if len(all_new) > 5:
                console.print(f"    ... and {len(all_new) - 5} more")

        if all_updated:
            console.print(f"  Files to update: {len(all_updated)}")
            for subdir, f, _, _ in all_updated[:5]:
                console.print(f"    [yellow]~[/yellow] {subdir}/{f}")
            if len(all_updated) > 5:
                console.print(f"    ... and {len(all_updated) - 5} more")

        # Confirm
        if Confirm and not Confirm.ask("\n  Apply these updates?", default=True):
            console.print("  Cancelled.")
            sys.exit(0)

        # Apply
        applied = 0
        for subdir, f, source_path, installed_path in all_new + all_updated:
            src = source_path / f
            dst = installed_path / f
            if copy_file(src, dst):
                applied += 1

        console.print(f"  [green]OK[/green] Applied {applied} file(s)")

    # Step 4: Refresh install metadata and bundled scripts
    #
    # Why: Some OPC runtime components (hooks) need to know where the canonical
    # opc/ project lives so they can run `uv` from the right directory.
    # The installer writes ~/.claude/opc_dir, but older installs won't have it
    # unless we also refresh it during updates.
    console.print("\n[bold]Step 4/6: Refreshing install metadata...[/bold]")

    # Persist the opc/ directory path for hooks (so uv runs in the correct project)
    try:
        resolved_opc = opc_dir.resolve()
        if (resolved_opc / "pyproject.toml").exists():
            (claude_dir / "opc_dir").write_text(str(resolved_opc) + "\n")
            console.print("  [green]OK[/green] Updated ~/.claude/opc_dir")
        else:
            console.print("  [yellow]WARN[/yellow] Could not locate pyproject.toml in opc/ - skipping opc_dir")
    except Exception as e:
        console.print(f"  [yellow]WARN[/yellow] Failed to write ~/.claude/opc_dir: {e}")

    # Copy bundled scripts referenced directly by settings.json (e.g., statusLine)
    installed_scripts = 0
    bundled_src_dir = integration_source / "scripts"
    target_scripts_dir = claude_dir / "scripts"
    for script_name in ["status.py", "status.sh"]:
        src = bundled_src_dir / script_name
        if src.exists():
            if copy_file(src, target_scripts_dir / script_name):
                installed_scripts += 1
    if installed_scripts:
        console.print(f"  [green]OK[/green] Installed/updated {installed_scripts} bundled script(s)")
    else:
        console.print("  [dim]No bundled scripts to install[/dim]")

    # Ensure mcp_config.json exists (used by runtime/mcp_client.py)
    # Do not overwrite an existing user config; if present, apply a small targeted fix for qlty.
    src_mcp_config = integration_source / "mcp_config.json"
    dst_mcp_config = claude_dir / "mcp_config.json"
    if not dst_mcp_config.exists():
        if src_mcp_config.exists():
            if copy_file(src_mcp_config, dst_mcp_config):
                console.print("  [green]OK[/green] Installed ~/.claude/mcp_config.json")
            else:
                console.print("  [yellow]WARN[/yellow] Failed to install ~/.claude/mcp_config.json")
        else:
            console.print("  [dim]No default mcp_config.json found in repo[/dim]")
    else:
        # Targeted migration: old qlty config used $HOME (not expanded) and a missing venv path.
        try:
            import json

            cfg = json.loads(dst_mcp_config.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
            qlty = servers.get("qlty") if isinstance(servers, dict) else None

            if isinstance(qlty, dict):
                cmd = str(qlty.get("command", ""))
                args = qlty.get("args", [])
                needs_fix = (
                    "$HOME/.claude/venv" in cmd
                    or (isinstance(args, list) and any(isinstance(a, str) and "$HOME/.claude/venv" in a for a in args))
                    or (isinstance(args, list) and any(isinstance(a, str) and "$HOME/.claude/servers/qlty/server.py" in a for a in args))
                )

                if needs_fix:
                    qlty["command"] = "python3"
                    qlty["args"] = ["${HOME}/.claude/servers/qlty/run_with_uv.py"]
                    dst_mcp_config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
                    console.print("  [green]OK[/green] Updated qlty MCP server config in ~/.claude/mcp_config.json")
        except Exception as e:
            console.print(f"  [yellow]WARN[/yellow] Could not update ~/.claude/mcp_config.json: {e}")

    # Ensure the qlty uv-runner is present (used by mcp_config.json)
    qlty_runner_src = integration_source / "servers" / "qlty" / "run_with_uv.py"
    qlty_runner_dst = claude_dir / "servers" / "qlty" / "run_with_uv.py"
    if qlty_runner_src.exists():
        if copy_file(qlty_runner_src, qlty_runner_dst):
            console.print("  [green]OK[/green] Installed/updated qlty runner (run_with_uv.py)")
        else:
            console.print("  [yellow]WARN[/yellow] Failed to install qlty runner (run_with_uv.py)")

    # Step 5: Update pip packages (TLDR, etc.)
    console.print("\n[bold]Step 5/6: Updating TLDR...[/bold]")

    # Check for local dev install first (monorepo setup)
    tldr_local_venv = opc_dir / "packages" / "tldr-code" / ".venv"
    tldr_local_pkg = opc_dir / "packages" / "tldr-code"

    if tldr_local_venv.exists() and (tldr_local_pkg / "pyproject.toml").exists():
        # Dev install - reinstall from local source (git pull already updated it)
        console.print("  [dim]Detected local dev install[/dim]")
        console.print("  Reinstalling from local source...")
        try:
            result = subprocess.run(
                ["uv", "pip", "install", "-e", "."],
                cwd=tldr_local_pkg,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                console.print("  [green]OK[/green] TLDR dev install updated")
            else:
                console.print(f"  [yellow]WARN[/yellow] {result.stderr[:100]}")
        except Exception as e:
            console.print(f"  [yellow]WARN[/yellow] {e}")
    elif shutil.which("tldr"):
        # PyPI install - update from PyPI
        console.print("  Updating from PyPI...")
        try:
            result = subprocess.run(
                ["uv", "pip", "install", "--upgrade", "llm-tldr"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                if "already satisfied" in result.stdout.lower():
                    console.print("  [dim]TLDR already up to date[/dim]")
                else:
                    console.print("  [green]OK[/green] TLDR updated")
            else:
                console.print(f"  [yellow]WARN[/yellow] {result.stderr[:100]}")
        except Exception as e:
            console.print(f"  [yellow]WARN[/yellow] {e}")
    else:
        console.print("  [dim]TLDR not installed, skipping[/dim]")

    # Step 6: Rebuild hooks if needed
    console.print("\n[bold]Step 6/6: Rebuilding TypeScript hooks...[/bold]")

    if ts_updated or all_new:
        hooks_dir = claude_dir / "hooks"
        success, msg = build_typescript_hooks(hooks_dir)
        if success:
            console.print(f"  [green]OK[/green] {msg}")
        else:
            console.print(f"  [yellow]WARN[/yellow] {msg}")
            console.print("  You can build manually: cd ~/.claude/hooks && npm run build")
    else:
        console.print("  [dim]No TypeScript changes, skipping build[/dim]")

    # Done
    console.print("\n[bold green]Update complete![/bold green]")


if __name__ == "__main__":
    try:
        run_update()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)
