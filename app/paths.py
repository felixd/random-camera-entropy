"""Filesystem locations shared by the Camera Entropy application."""
from __future__ import annotations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "app"
WEB_ROOT = APP_ROOT / "web"
TEMPLATES_ROOT = WEB_ROOT / "templates"
STATIC_ROOT = WEB_ROOT / "static"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
DATA_ROOT_DEFAULT = PROJECT_ROOT / "data"
