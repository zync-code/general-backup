"""restore-agent subcommand — agent-driven restore via Claude Code.

Steps:
  1. Validate the bundle exists and is a valid tarball.
  2. Extract manifest.json to a temp staging dir.
  3. Run 'general-backup verify <bundle>' — abort if it fails.
  4. Locate docs/restore-runbook.md (embedded in bundle OR repo).
  5. Spawn a tmux session running:
       claude --dangerously-skip-permissions -p "<runbook>"
     with BUNDLE_PATH, MANIFEST_PATH, AGE_IDENTITY, GB_BIN, TARGET_USER injected.
  6. Stream the agent log to stdout + /var/log/general-backup-restore.log.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from ..log import error, info, warn


_LOG_PATH = Path("/var/log/general-backup-restore.log")
_SESSION_NAME = "gb-restore-agent"


def run(args) -> int:
    bundle = Path(args.bundle)
    age_identity: str = getattr(args, "age_identity", None) or ""
    auto_confirm: bool = getattr(args, "auto_confirm", False)

    if not bundle.is_file():
        error(f"bundle not found: {bundle}")
        return 1

    # ── 1. Verify bundle ──────────────────────────────────────────────────────
    info("restore-agent: verifying bundle integrity")
    gb_bin = _find_gb_bin()
    verify_cmd = [gb_bin, "verify", str(bundle)]
    if age_identity:
        verify_cmd += ["--age-identity", age_identity]

    result = subprocess.run(verify_cmd, capture_output=False)
    if result.returncode != 0:
        error(f"bundle verification failed (exit {result.returncode}) — aborting")
        return 2

    # ── 2. Extract manifest.json ──────────────────────────────────────────────
    staging_dir = Path(tempfile.mkdtemp(prefix="gb-restore-agent-"))
    info(f"restore-agent: staging dir: {staging_dir}")

    result = subprocess.run(
        ["tar", "-xf", str(bundle), "-C", str(staging_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        error(f"failed to extract bundle: {result.stderr.strip()}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 2

    tops = [p for p in staging_dir.iterdir() if p.is_dir()]
    if not tops:
        error("bundle appears empty after extraction")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 2

    bundle_root = tops[0]
    manifest_path = bundle_root / "manifest.json"
    if not manifest_path.exists():
        error("manifest.json not found in extracted bundle")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 2

    info(f"restore-agent: manifest at {manifest_path}")

    # ── 3. Locate runbook ─────────────────────────────────────────────────────
    # Prefer the runbook embedded in the bundle (captured at bundle creation
    # time); fall back to the current repo version.
    runbook_path = bundle_root / "restore-runbook.md"
    if not runbook_path.exists():
        repo_runbook = Path(__file__).resolve().parent.parent.parent / "docs" / "restore-runbook.md"
        if repo_runbook.exists():
            runbook_path = repo_runbook
            warn("restore-agent: runbook not found in bundle — using repo version")
        else:
            error("restore-runbook.md not found in bundle or repo docs/")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return 1

    runbook_text = runbook_path.read_text(encoding="utf-8")
    info(f"restore-agent: runbook: {runbook_path} ({len(runbook_text)} chars)")

    # ── 4. Ensure log file is writable ───────────────────────────────────────
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOG_PATH.touch(exist_ok=True)
    except PermissionError:
        warn(f"restore-agent: cannot write to {_LOG_PATH} — log will be stdout only")

    # ── 5. Build tmux session ─────────────────────────────────────────────────
    if not shutil.which("tmux"):
        error("tmux is not installed — required for restore-agent")
        return 1

    if not shutil.which("claude"):
        error(
            "claude CLI is not installed — run './bootstrap.sh' first to install it, "
            "or use 'general-backup restore' for script-mode restore"
        )
        return 1

    env = {
        **os.environ,
        "BUNDLE_PATH": str(bundle),
        "MANIFEST_PATH": str(manifest_path),
        "AGE_IDENTITY": age_identity,
        "GB_BIN": gb_bin,
        "TARGET_USER": getattr(args, "target_user", "bot"),
    }

    # Kill any existing session with the same name
    subprocess.run(
        ["tmux", "kill-session", "-t", _SESSION_NAME],
        capture_output=True,
    )

    # The claude invocation: pipe runbook as the -p prompt, redirect output to log
    claude_cmd = " ".join([
        "claude",
        "--dangerously-skip-permissions",
        "-p", shlex_quote(runbook_text),
    ])
    if auto_confirm:
        claude_cmd += " --auto-confirm"

    log_cmd = f"tee -a {shlex_quote(str(_LOG_PATH))}"
    full_cmd = f"{claude_cmd} 2>&1 | {log_cmd}"

    info(f"restore-agent: spawning tmux session '{_SESSION_NAME}'")
    result = subprocess.run(
        [
            "tmux", "new-session", "-d",
            "-s", _SESSION_NAME,
            "-x", "220", "-y", "50",
            "--",
            "bash", "-c", full_cmd,
        ],
        env=env,
    )

    if result.returncode != 0:
        error("failed to create tmux session")
        return 1

    info(
        f"restore-agent: agent session started. "
        f"Attach: tmux attach -t {_SESSION_NAME}"
    )
    info(f"restore-agent: log streaming to {_LOG_PATH} and stdout")

    # ── 6. Stream log to stdout ───────────────────────────────────────────────
    _stream_log(_LOG_PATH, _SESSION_NAME)

    return 0


def _stream_log(log_path: Path, session_name: str) -> None:
    """Tail the log file until the tmux session exits."""
    try:
        proc = subprocess.Popen(
            ["tail", "-f", str(log_path)],
            stdout=sys.stdout,
            stderr=subprocess.DEVNULL,
        )
        # Poll until the tmux session ends
        while True:
            check = subprocess.run(
                ["tmux", "has-session", "-t", session_name],
                capture_output=True,
            )
            if check.returncode != 0:
                break
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        info("restore-agent: interrupted — agent session still running in tmux")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


def shlex_quote(s: str) -> str:
    """Shell-escape a string for inclusion in a bash -c argument."""
    import shlex
    return shlex.quote(s)


def _find_gb_bin() -> str:
    found = shutil.which("general-backup")
    if found:
        return found
    local = Path(__file__).resolve().parent.parent.parent / "bin" / "general-backup"
    if local.exists():
        return str(local)
    return "general-backup"
