"""Version of the running application.

The release number is in the VERSION file. docker/install.sh adds the build (number of commits, short
commit id, date) in build_info.json before building the image, so the page footer shows exactly which
code is running, e.g. "v1.0.151 (b9a1d70, 2026-11-21)". Outside Docker it is read from git.
"""

import json
import subprocess
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _git(*args):
    try:
        out = subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


@lru_cache(maxsize=1)
def build_info():
    release = "0"
    try:
        release = (BASE_DIR / "VERSION").read_text().strip() or release
    except OSError:
        pass
    info = {"release": release, "count": None, "commit": None, "date": None}
    try:
        info.update({k: v for k, v in json.loads((BASE_DIR / "build_info.json").read_text()).items() if v})
    except (OSError, ValueError):
        info["count"] = _git("rev-list", "--count", "HEAD")
        info["commit"] = _git("rev-parse", "--short", "HEAD")
        info["date"] = (_git("log", "-1", "--format=%cs") or None)
        if info["commit"] and _git("status", "--porcelain"):
            info["commit"] += "+"        # uncommitted changes
    return info


def version_string():
    """'v1.0.151 (b9a1d70, 2026-11-21)', or just 'v1.0' when the build is unknown."""
    info = build_info()
    number = f"v{info['release']}" + (f".{info['count']}" if info["count"] else "")
    details = ", ".join(x for x in (info["commit"], info["date"]) if x)
    return f"{number} ({details})" if details else number
