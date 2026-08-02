#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

REPORT_NAMES = (
    "global_campaign_report.html",
    "lsb_campaign_report.html",
    "qualification_report.html",
    "dual_weave_campaign_report.html",
    "spatial_campaign_report.html",
    "run_report.html",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def step_root(root: Path, output: str) -> Path | None:
    value = Path(output)
    parts = list(value.parts)
    if parts and parts[0] == root.name:
        parts = parts[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    candidate = (root / Path(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def find_report(root: Path, item: dict[str, Any]) -> str:
    target = step_root(root, str(item.get("output", "")))
    if target is None:
        return ""
    for name in REPORT_NAMES:
        path = target / name
        if path.is_file():
            return path.relative_to(root).as_posix()
    return ""


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    steps: list[dict[str, Any]] = []
    for state in sorted((root / "global_steps").glob("*.json")):
        item = read_json(state)
        if not item:
            continue
        item = dict(item)
        item["report"] = find_report(root, item)
        steps.append(item)
    summary = {
        "schema": "camera-entropy-global-campaign-v1",
        "campaign": root.name,
        "total": len(steps),
        "steps": steps,
        "passed": sum(item.get("status") == "passed" for item in steps),
        "failed": sum(item.get("status") == "failed" for item in steps),
        "skipped": sum(item.get("status") == "skipped" for item in steps),
    }
    (root / "global_campaign_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index', '')))}</td>"
        f"<td>{html.escape(str(item.get('profile', '')))}</td>"
        f"<td>{html.escape(str(item.get('script', '')))}</td>"
        f"<td>{html.escape(str(item.get('status', '')))}</td>"
        f"<td>{html.escape(str(item.get('exit_code', '') if item.get('exit_code') is not None else ''))}</td>"
        f"<td><code>{html.escape(str(item.get('output', '')))}</code></td>"
        + (
            f'<td><a href="{html.escape(str(item.get("report", "")))}">raport</a></td>'
            if item.get("report")
            else "<td>—</td>"
        )
        + f'<td><a href="global_steps/{html.escape(str(item.get("log", "")))}">log</a></td>'
        + "</tr>"
        for item in steps
    )
    report = f"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Globalna kampania</title><style>body{{font-family:system-ui;background:#0b0f14;color:#edf4fb;margin:0;padding:24px}}a{{color:#55b5ff}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #2a3441;text-align:left;white-space:nowrap}}section{{background:#131a22;border:1px solid #2a3441;border-radius:12px;padding:16px;overflow:auto}}</style></head><body><h1>Globalna kampania testowa</h1><p>{html.escape(root.name)} · total {summary['total']} · passed {summary['passed']} · failed {summary['failed']} · skipped {summary['skipped']}</p><section><table><thead><tr><th>#</th><th>Profil</th><th>Skrypt</th><th>Status</th><th>RC</th><th>Wyjście</th><th>Raport</th><th>Log</th></tr></thead><tbody>{rows}</tbody></table></section></body></html>"""
    (root / "global_campaign_report.html").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
