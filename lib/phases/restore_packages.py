"""Restore phase: packages — replay apt package selections."""
from __future__ import annotations

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, run_cmd


def run(ctx: RestoreContext) -> None:
    selections = ctx.packages_path / "apt-selections.txt"
    if not selections.exists():
        warn("restore/packages: apt-selections.txt not found in bundle — skipping")
        return

    info("restore/packages: applying dpkg --set-selections")
    try:
        import subprocess
        with open(selections, "rb") as f:
            subprocess.run(
                ["dpkg", "--set-selections"],
                stdin=f,
                check=True,
            )
    except Exception as exc:
        raise RestoreError(f"dpkg --set-selections failed: {exc}")

    info("restore/packages: running apt-get dselect-upgrade")
    try:
        run_cmd(
            ["apt-get", "update", "-q"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        run_cmd(
            ["apt-get", "dselect-upgrade", "-y", "-q"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
    except Exception as exc:
        raise RestoreError(f"apt-get dselect-upgrade failed: {exc}")

    info("restore/packages: packages applied")

    _restore_pip(ctx)


def _restore_pip(ctx: RestoreContext) -> None:
    """Best-effort reinstall of pip-managed packages from pip3-freeze.txt.

    Not all entries are real installable packages (e.g. stray non-pypi names
    that show up in some environments) and a few commonly conflict with
    Debian-shipped dist-packages (urllib3, idna — pip refuses to uninstall
    them because there's no RECORD file). Neither case should abort the
    whole restore, so this installs everything it can and just warns about
    individual failures rather than raising.
    """
    import subprocess

    freeze = ctx.packages_path / "pip3-freeze.txt"
    if not freeze.exists():
        warn("restore/packages: pip3-freeze.txt not found in bundle — skipping pip restore")
        return

    info("restore/packages: pip3 install -r pip3-freeze.txt (best-effort)")
    result = subprocess.run(
        ["pip3", "install", "--break-system-packages", "-r", str(freeze)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Drop lines naming packages pip reported it cannot touch, then retry once.
        cannot_uninstall = set()
        no_distribution = set()
        for line in result.stderr.splitlines():
            line = line.strip()
            if line.startswith("ERROR: Cannot uninstall "):
                # ERROR: Cannot uninstall <pkg> <version>, RECORD file not found...
                cannot_uninstall.add(line.split("ERROR: Cannot uninstall ")[1].split()[0].lower())
            elif line.startswith("ERROR: No matching distribution found for "):
                no_distribution.add(line.split("for ")[1].split("==")[0].lower())

        skip = cannot_uninstall | no_distribution
        if skip:
            warn(f"restore/packages: pip install hit {len(skip)} conflicting/missing package(s) "
                 f"({', '.join(sorted(skip))}) — retrying without them")
            filtered = [
                line for line in freeze.read_text().splitlines()
                if line.split("==")[0].strip().lower() not in skip
            ]
            retry = subprocess.run(
                ["pip3", "install", "--break-system-packages", "-"],
                input="\n".join(filtered), capture_output=True, text=True,
            )
            if retry.returncode != 0:
                warn(f"restore/packages: pip install retry still had failures: {retry.stderr[-500:]}")
            else:
                info("restore/packages: pip packages installed (after excluding conflicts)")
        else:
            warn(f"restore/packages: pip install failed: {result.stderr[-500:]}")
    else:
        info("restore/packages: pip packages installed")
