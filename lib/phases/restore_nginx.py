"""Restore phase: nginx — copy config and reload."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    nginx_src = ctx.data / "nginx"
    if not nginx_src.exists():
        warn("restore/nginx: no nginx data in bundle — skipping")
        return

    nginx_conf_dir = Path("/etc/nginx")

    # nginx.conf
    src_conf = nginx_src / "nginx.conf"
    if src_conf.exists():
        shutil.copy2(src_conf, nginx_conf_dir / "nginx.conf")
        info("restore/nginx: installed nginx.conf")

    # sites-available/
    src_sites_avail = nginx_src / "sites-available"
    if src_sites_avail.exists():
        dest_sa = nginx_conf_dir / "sites-available"
        dest_sa.mkdir(parents=True, exist_ok=True)
        for f in src_sites_avail.iterdir():
            shutil.copy2(f, dest_sa / f.name)
        info(f"restore/nginx: installed {len(list(src_sites_avail.iterdir()))} site(s) to sites-available/")

    # conf.d/
    src_confd = nginx_src / "conf.d"
    if src_confd.exists():
        dest_cd = nginx_conf_dir / "conf.d"
        dest_cd.mkdir(parents=True, exist_ok=True)
        for f in src_confd.iterdir():
            shutil.copy2(f, dest_cd / f.name)
        info(f"restore/nginx: installed {len(list(src_confd.iterdir()))} file(s) to conf.d/")

    # sites-enabled symlinks from sites-enabled.txt
    sites_enabled_txt = nginx_src / "sites-enabled.txt"
    if sites_enabled_txt.exists():
        _restore_symlinks(sites_enabled_txt, nginx_conf_dir)

    # Test and reload
    result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RestoreError(
            f"nginx -t failed after restore:\n{result.stderr.strip()}\n"
            "Check for missing SSL certificates (obtain with certbot)"
        )

    info("restore/nginx: nginx -t ok — reloading")
    result = subprocess.run(
        ["systemctl", "reload", "nginx"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Try restart if reload fails (nginx might not be running yet)
        subprocess.run(["systemctl", "start", "nginx"], capture_output=True)

    info("restore/nginx: done")


def _restore_symlinks(txt_path: Path, nginx_dir: Path) -> None:
    sites_enabled = nginx_dir / "sites-enabled"
    sites_enabled.mkdir(parents=True, exist_ok=True)
    sites_available = nginx_dir / "sites-available"

    # Remove existing symlinks
    for existing in sites_enabled.iterdir():
        if existing.is_symlink():
            existing.unlink()

    created = 0
    for line in txt_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: <link_name> -> <target> or just <link_name>
        if " -> " in line:
            link_name, _, target = line.partition(" -> ")
            link_name = link_name.strip()
            target_path = Path(target.strip())
        else:
            link_name = line
            target_path = sites_available / line

        link = sites_enabled / link_name
        if not link.exists():
            link.symlink_to(target_path)
            created += 1

    if created:
        info(f"restore/nginx: recreated {created} sites-enabled symlink(s)")
