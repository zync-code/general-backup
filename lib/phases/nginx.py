"""Capture phase: nginx — copy config and record sites-enabled symlink map."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..log import info, warn
from . import Context

_NGINX_DIR = Path("/etc/nginx")


def run(ctx: Context) -> None:
    nginx_dest = ctx.ensure_dir("data", "nginx")

    if not _NGINX_DIR.exists():
        warn("nginx: /etc/nginx not found — skipping")
        return

    # nginx.conf
    nginx_conf = _NGINX_DIR / "nginx.conf"
    if nginx_conf.exists():
        shutil.copy2(nginx_conf, nginx_dest / "nginx.conf")
        info("nginx: copied nginx.conf")

    # sites-available/
    sites_avail = _NGINX_DIR / "sites-available"
    if sites_avail.exists():
        dest_sa = nginx_dest / "sites-available"
        shutil.copytree(sites_avail, dest_sa, dirs_exist_ok=True)
        count = len(list(dest_sa.iterdir()))
        info(f"nginx: copied sites-available/ ({count} file(s))")

    # conf.d/
    conf_d = _NGINX_DIR / "conf.d"
    if conf_d.exists():
        dest_cd = nginx_dest / "conf.d"
        shutil.copytree(conf_d, dest_cd, dirs_exist_ok=True)
        count = len(list(dest_cd.iterdir()))
        info(f"nginx: copied conf.d/ ({count} file(s))")

    # Record sites-enabled symlink targets
    sites_enabled = _NGINX_DIR / "sites-enabled"
    vhost_count = 0
    if sites_enabled.exists():
        lines = []
        for entry in sorted(sites_enabled.iterdir()):
            if entry.is_symlink():
                target = entry.resolve()
                lines.append(f"{entry.name} -> {target}")
                vhost_count += 1
            elif entry.is_file():
                lines.append(entry.name)
                vhost_count += 1
        (nginx_dest / "sites-enabled.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        info(f"nginx: recorded {vhost_count} sites-enabled entry/entries")

    ctx.manifest.components["nginx"] = {
        "vhost_count": vhost_count,
        "include_count": _count_includes(nginx_dest),
    }
    info("nginx: done")


def _count_includes(nginx_dir: Path) -> int:
    total = 0
    for sub in nginx_dir.rglob("*"):
        if sub.is_file():
            total += 1
    return total
