#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a read-only comparison index for a spatial smoke campaign."""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_VERSION = "2026.08.05.camera-entropy-spatial-campaign.8.0.1"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    args = parser.parse_args()
    root = args.campaign_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda p: p.name):
        config = read_json(path / "runner_config.json")
        complete = (path / "READY.json").exists() or (path / "run_report.html").exists()
        failed = (path / "run_failed.json").exists()
        status = "failed" if failed else "complete" if complete else "incomplete"
        report = "run_report.html" if (path / "run_report.html").exists() else ""
        rows.append({
            "name": path.name,
            "status": status,
            "report": report,
            "pattern": config.get("spatial_mask_pattern", "—"),
            "step": [config.get("spatial_step_x", "—"), config.get("spatial_step_y", "—")],
            "phase": [config.get("spatial_phase_x", "—"), config.get("spatial_phase_y", "—")],
            "offset": [config.get("temporal_spatial_offset_x", "—"), config.get("temporal_spatial_offset_y", "—")],
            "order": config.get("serialization_order", "—"),
        })

    complete_count = sum(item["status"] == "complete" for item in rows)
    failed_count = sum(item["status"] == "failed" for item in rows)
    summary = {
        "app_version": APP_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_profiles": len(rows),
        "complete_profiles": complete_count,
        "failed_profiles": failed_count,
        "profiles": rows,
    }
    (root / "spatial_campaign_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    table = []
    for item in rows:
        name = html.escape(str(item["name"]))
        report_link = f'<a href="{name}/run_report.html">raport</a>' if item["report"] else "—"
        table.append(
            "<tr>"
            f"<td><a href=\"{name}/\">{name}</a></td>"
            f"<td>{html.escape(item['status'])}</td>"
            f"<td>{html.escape(str(item['pattern']))}</td>"
            f"<td>{html.escape(str(item['step']))}</td>"
            f"<td>{html.escape(str(item['phase']))}</td>"
            f"<td>{html.escape(str(item['offset']))}</td>"
            f"<td>{html.escape(str(item['order']))}</td>"
            f"<td>{report_link}</td>"
            "</tr>"
        )
    document = f'''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Spatial campaign</title><style>:root{{color-scheme:dark}}body{{font-family:system-ui;background:#0b0f14;color:#edf4fb;margin:0;padding:24px}}a{{color:#55b5ff}}table{{width:100%;border-collapse:collapse;background:#131a22}}th,td{{border:1px solid #2a3441;padding:8px;text-align:left}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}.card{{background:#131a22;border:1px solid #2a3441;border-radius:8px;padding:12px}}</style></head><body><h1>Spatial sampling campaign</h1><p>{html.escape(APP_VERSION)}</p><div class="cards"><div class="card">Profile: {len(rows)}</div><div class="card">Complete: {complete_count}</div><div class="card">Failed: {failed_count}</div></div><p>Raport jest indeksem przebiegów. Wybór najlepszego wariantu powinien opierać się na RAW przed SHA3, korelacjach wielu lagów, heatmapach oraz przepustowości.</p><table><thead><tr><th>Run</th><th>Status</th><th>Maska</th><th>Step</th><th>Faza</th><th>Offset</th><th>Serializacja</th><th>Raport</th></tr></thead><tbody>{''.join(table)}</tbody></table></body></html>'''
    (root / "spatial_campaign_report.html").write_text(document, encoding="utf-8")
    (root / "READY.json").write_text(
        json.dumps({"status": "ready", "app_version": APP_VERSION}, indent=2), encoding="utf-8"
    )
    print(f"spatial campaign report: {root / 'spatial_campaign_report.html'}")
    return 0 if failed_count == 0 and len(rows) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
