#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a compact comparison report for a parallel spatial-sampling run."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path
from typing import Any

VARIANTS = (
    ("full", "Pełna maska", "analysis_vn"),
    ("checkerboard-even", "Checkerboard parzysty", "analysis_y_temporal_vn_checkerboard_even"),
    ("checkerboard-odd", "Checkerboard nieparzysty", "analysis_y_temporal_vn_checkerboard_odd"),
    ("grid2x2", "Siatka 2×2", "analysis_y_temporal_vn_grid2x2"),
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fmt(value: Any, digits: int = 6) -> str:
    number = finite(value)
    return "n/a" if number is None else f"{number:.{digits}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    run = args.run_dir.resolve()
    complete = load_json(run / "output_complete.json")
    status_by_variant = complete.get("spatial_variants", {})

    rows: list[dict[str, Any]] = []
    for key, label, analysis_dir in VARIANTS:
        summary = load_json(run / analysis_dir / "summary.json")
        status = status_by_variant.get(key, {})
        corr = status.get("pixel_correlation", {}) or {}
        health = status.get("health", {}) or {}
        lag1 = summary.get("lag1", {}) or {}
        rows.append(
            {
                "variant": key,
                "label": label,
                "total_bytes": summary.get("total_bytes"),
                "output_bps": status.get("output_bps_lifetime"),
                "p1": summary.get("p1"),
                "lag1_phi": lag1.get("phi"),
                "byte_hmin": summary.get("byte_min_entropy_bits_per_byte"),
                "byte_chi_square": summary.get("byte_chi_square"),
                "byte_chi_square_p": summary.get("byte_chi_square_p_value"),
                "monte_carlo_pi": summary.get("monte_carlo_pi"),
                "most_common_byte": summary.get("most_common_byte_hex"),
                "spatial_max_abs_phi": corr.get("max_abs_phi"),
                "spatial_worst_direction": corr.get("worst_direction"),
                "spatial_worst_distance": corr.get("worst_distance"),
                "rct_failures": health.get("rct_failures"),
                "apt_failures": health.get("apt_failures"),
                "health_latched": health.get("latched"),
            }
        )

    out_dir = run / "spatial_comparison_report"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "spatial_comparison.json"
    csv_path = out_dir / "spatial_comparison.csv"
    html_path = out_dir / "index.html"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    table_rows = []
    for row in rows:
        health = "FAIL" if row["health_latched"] else "PASS"
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(row['label'])}</td>"
            f"<td>{row['total_bytes'] or 'n/a'}</td>"
            f"<td>{fmt(row['output_bps'], 1)}</td>"
            f"<td>{fmt(row['p1'], 8)}</td>"
            f"<td>{fmt(row['lag1_phi'], 8)}</td>"
            f"<td>{fmt(row['byte_hmin'], 6)}</td>"
            f"<td>{fmt(row['byte_chi_square'], 2)}</td>"
            f"<td>{fmt(row['byte_chi_square_p'], 6)}</td>"
            f"<td>{fmt(row['spatial_max_abs_phi'], 8)}</td>"
            f"<td>{html.escape(str(row['spatial_worst_direction'] or 'n/a'))}/"
            f"{html.escape(str(row['spatial_worst_distance'] or 'n/a'))}</td>"
            f"<td class='{health.lower()}'>{health}</td>"
            "</tr>"
        )

    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Porównanie próbkowania przestrzennego</title>
<style>
body{{font:15px system-ui,sans-serif;margin:24px;background:#f5f7fa;color:#172033}}
main{{max-width:1500px;margin:auto;background:white;padding:24px;border-radius:14px;box-shadow:0 4px 22px #0001}}
h1{{margin-top:0}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 10px;border-bottom:1px solid #dfe5ee;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{position:sticky;top:0;background:#eef3f9}}
.pass{{color:#08752f;font-weight:700}}.fail{{color:#b00020;font-weight:700}} code{{background:#eef3f9;padding:2px 5px}}
.small{{color:#556070}} canvas{{width:100%;height:280px;margin-top:24px}}
</style></head><body><main>
<h1>Porównanie próbkowania przestrzennego</h1>
<p class="small">Katalog: <code>{html.escape(str(run))}</code>. Wszystkie warianty powstały równolegle z tych samych par klatek.</p>
<table><thead><tr><th>Wariant</th><th>Bajty</th><th>bit/s</th><th>P(1)</th><th>φ lag-1</th><th>Hmin bajtu</th><th>χ²</th><th>p(χ²)</th><th>max |φ| pikseli</th><th>najgorszy kierunek/d</th><th>Health</th></tr></thead>
<tbody>{''.join(table_rows)}</tbody></table>
<canvas id="chart" width="1200" height="280"></canvas>
<script>
const rows={payload};
const c=document.getElementById('chart'),x=c.getContext('2d');
x.clearRect(0,0,c.width,c.height); x.font='15px system-ui';
const vals=rows.map(r=>Math.abs(Number(r.lag1_phi)||0)); const max=Math.max(...vals,1e-9);
const left=220,right=30,top=25,rowH=55,w=c.width-left-right;
rows.forEach((r,i)=>{{const y=top+i*rowH;x.fillStyle='#172033';x.fillText(r.label,10,y+19);x.fillStyle='#4477aa';x.fillRect(left,y,w*vals[i]/max,24);x.fillStyle='#172033';x.fillText(vals[i].toFixed(8),left+Math.min(w*vals[i]/max+8,w-90),y+18);}});
x.fillText('Bezwzględna korelacja bitowa lag-1 (mniej = lepiej)',left,c.height-8);
</script></main></body></html>"""
    html_path.write_text(page, encoding="utf-8")
    print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
