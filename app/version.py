"""Single source of release/component version strings."""
from __future__ import annotations

from functools import lru_cache

from .paths import PROJECT_ROOT


@lru_cache(maxsize=1)
def release_version() -> str:
    """Return the semantic release suffix, e.g. ``8.0.0``."""
    raw = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    marker = "camera-entropy-distributed."
    if marker in raw:
        return raw.split(marker, 1)[1]
    return raw or "unknown"


def component_version(component: str) -> str:
    return f"2026.08.06.{component}.{release_version()}"
