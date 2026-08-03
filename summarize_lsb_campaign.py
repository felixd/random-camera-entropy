#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the comparison report for the exhaustive 1..8 LSB campaign.

Throughput fields come from the terminal worker report.  Empirical entropy-rate
fields are diagnostics calculated from one dataset; they are not SP 800-90B
entropy claims.  Credited entropy rate uses the externally supplied
``ENTROPY_CREDIT_BITS_PER_PIXEL`` budget.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

APP_VERSION = "2026.08.03.camera-entropy-lsb-campaign.7.10.0"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nested(record: dict[str, Any], *keys: str) -> Any:
    current: Any = record
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current



def select_rate(record: dict[str, Any], *paths: tuple[str, ...]) -> tuple[float | None, str]:
    """Select the first positive rate and report its measurement basis."""
    for path in paths:
        value = finite_number(nested(record, *path))
        if value is not None and value > 0.0:
            return value, path[-1]
    return None, ""


def profile_rows(root: Path) -> list[dict[str, Any]]:
    state_dir = root / "lsb_profiles"
    state_files = sorted(state_dir.glob("*.json")) if state_dir.is_dir() else []
    rows: list[dict[str, Any]] = []

    if state_files:
        sources: list[tuple[str, dict[str, Any]]] = []
        for path in state_files:
            state = read_json(path)
            sources.append((str(state.get("profile") or path.stem), state))
    else:
        # Compatibility with campaigns created before explicit profile-state files.
        sources = [
            (path.name, {})
            for path in sorted(root.iterdir())
            if path.is_dir() and path.name != "lsb_profiles"
        ]

    for profile, state_record in sources:
        run = root / profile
        config = read_json(run / "runner_config.json")
        result = read_json(run / "runner_summary.json")
        complete_report = read_json(run / "output_complete.json")
        failed_report = read_json(run / "run_failed.json")
        terminal = complete_report or failed_report
        analysis = read_json(run / "analysis_conditioned" / "summary.json")
        bitplanes = read_json(run / "lsb_bitplane_summary.json")

        # Files written by the worker are authoritative.  Profile state can be
        # stale if the shell was interrupted between worker exit and state update.
        status = (
            "failed"
            if failed_report
            else (
                "complete"
                if complete_report
                else str(state_record.get("status") or "unknown")
            )
        )

        lsb_bits = int(state_record.get("lsb_bits", config.get("lsb_bits", 1)) or 1)
        credit = finite_number(
            state_record.get(
                "entropy_credit_bits_per_pixel",
                config.get("entropy_credit_bits_per_pixel"),
            )
        )
        masked_bps, masked_rate_basis = select_rate(
            terminal,
            ("rates", "masked_bps_lifetime"),
            ("rates", "masked_bps_10s"),
            ("rates", "masked_bps_current"),
        )
        raw_bps, raw_rate_basis = select_rate(
            terminal,
            ("rates", "raw_change_bps_lifetime"),
            ("rates", "raw_change_bps_10s"),
            ("rates", "raw_change_bps_current"),
        )
        pixel_symbols_per_second = (
            masked_bps / lsb_bits
            if masked_bps is not None and masked_bps >= 0.0 and lsb_bits > 0
            else None
        )
        symbol_hmin = finite_number(bitplanes.get("symbol_min_entropy_bits_per_symbol"))
        empirical_hmin_bps = (
            pixel_symbols_per_second * symbol_hmin
            if pixel_symbols_per_second is not None and symbol_hmin is not None
            else None
        )
        credited_entropy_bps = (
            pixel_symbols_per_second * credit
            if pixel_symbols_per_second is not None and credit is not None
            else None
        )
        conditioned_output_bps = finite_number(
            nested(terminal, "conditioner", "output_bps_until_complete")
        )
        credit_utilization = (
            conditioned_output_bps / credited_entropy_bps
            if conditioned_output_bps is not None
            and credited_entropy_bps is not None
            and credited_entropy_bps > 0.0
            else None
        )
        health = terminal.get("health", {}) if isinstance(terminal.get("health"), dict) else {}
        failure_reason = str(failed_report.get("reason") or "")

        rows.append(
            {
                "profile": profile,
                "status": status,
                "exit_code": state_record.get("exit_code"),
                "sample_mode": state_record.get("sample_mode", config.get("sample_mode")),
                "lsb_bits": lsb_bits,
                "entropy_credit_bits_per_pixel": credit,
                "minimum_conditioner_input_bits": state_record.get(
                    "minimum_conditioner_input_bits",
                    config.get("minimum_conditioner_input_bits"),
                ),
                "conditioner_input_bits": state_record.get(
                    "conditioner_input_bits",
                    config.get("conditioner_input_bits"),
                ),
                "active_pixels": terminal.get("active_pixels"),
                "raw_input_bps": raw_bps,
                "masked_input_bps": masked_bps,
                "raw_rate_basis": raw_rate_basis,
                "masked_rate_basis": masked_rate_basis,
                # Compatibility aliases for consumers of campaign schema v2.
                "raw_input_bps_10s": raw_bps,
                "masked_input_bps_10s": masked_bps,
                "pixel_symbols_per_second": pixel_symbols_per_second,
                "credited_entropy_bps": credited_entropy_bps,
                "empirical_symbol_hmin_bps": empirical_hmin_bps,
                "conditioned_output_bps": conditioned_output_bps,
                "conditioned_vs_credited_entropy": credit_utilization,
                "time_to_target_seconds": finite_number(
                    nested(terminal, "conditioner", "time_to_target_seconds")
                ),
                "conditioned_written_bytes": nested(terminal, "conditioner", "written_bytes"),
                "conditioned_input_bits_consumed": nested(
                    terminal, "conditioner", "input_bits_consumed"
                ),
                "conditioned_total_bytes": analysis.get("total_bytes"),
                "conditioned_byte_min_entropy": analysis.get(
                    "byte_min_entropy_bits_per_byte"
                ),
                "conditioned_p1": analysis.get("p1"),
                "conditioned_lag1": analysis.get("lag1"),
                "symbol_min_entropy_bits_per_symbol": symbol_hmin,
                "symbol_min_entropy_bits_per_input_bit": bitplanes.get(
                    "symbol_min_entropy_bits_per_input_bit"
                ),
                "max_abs_cross_plane_phi": bitplanes.get("max_abs_cross_plane_phi"),
                "max_cross_plane_mutual_information_bits": bitplanes.get(
                    "max_cross_plane_mutual_information_bits"
                ),
                "bitplane_report": (
                    f"{profile}/lsb_bitplane_report.html"
                    if (run / "lsb_bitplane_report.html").exists()
                    else ""
                ),
                "state": terminal.get("state") or terminal.get("status") or result.get("status"),
                "failure_reason": failure_reason,
                "health_sample_domain": health.get("sample_domain"),
                "health_sample_width_bits": health.get("sample_width_bits"),
                "health_rct_failures": health.get("rct_failures"),
                "health_apt_failures": health.get("apt_failures"),
                "health_rct_cutoff": health.get("rct_cutoff"),
                "health_apt_cutoff": health.get("apt_cutoff"),
                "profile_log": f"{profile}.profile.log" if (root / f"{profile}.profile.log").exists() else "",
                "failure_json": f"{profile}/run_failed.json" if failed_report else "",
                "report": (
                    f"{profile}/run_report.html"
                    if (run / "run_report.html").exists()
                    else ""
                ),
            }
        )

    baseline = next(
        (
            row
            for row in rows
            if row.get("sample_mode") == "xor" and row.get("lsb_bits") == 1
        ),
        None,
    )
    baseline_masked = finite_number(baseline.get("masked_input_bps")) if baseline else None
    baseline_conditioned = finite_number(baseline.get("conditioned_output_bps")) if baseline else None
    for row in rows:
        masked = finite_number(row.get("masked_input_bps"))
        conditioned = finite_number(row.get("conditioned_output_bps"))
        row["masked_throughput_vs_xor_lsb1"] = (
            masked / baseline_masked
            if masked is not None and baseline_masked and baseline_masked > 0.0
            else None
        )
        row["conditioned_throughput_vs_xor_lsb1"] = (
            conditioned / baseline_conditioned
            if conditioned is not None and baseline_conditioned and baseline_conditioned > 0.0
            else None
        )
    return rows


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.9g}"
    return str(value)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    rows = profile_rows(root)
    complete = sum(row["status"] == "complete" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    incomplete = len(rows) - complete - failed
    summary = {
        "schema": "camera-entropy-lsb-campaign-v3",
        "app_version": APP_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign": root.name,
        "total_profiles": len(rows),
        "profiles": rows,
        "complete": complete,
        "failed": failed,
        "incomplete": incomplete,
        "warning": (
            "Empirical entropy-rate values are single-dataset diagnostics, not an "
            "SP 800-90B min-entropy claim. Credited entropy rate is based on the "
            "externally supplied entropy-credit budget."
        ),
    }
    (root / "lsb_campaign_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "profile",
        "status",
        "exit_code",
        "sample_mode",
        "lsb_bits",
        "entropy_credit_bits_per_pixel",
        "minimum_conditioner_input_bits",
        "conditioner_input_bits",
        "active_pixels",
        "raw_input_bps",
        "masked_input_bps",
        "raw_rate_basis",
        "masked_rate_basis",
        "raw_input_bps_10s",
        "masked_input_bps_10s",
        "pixel_symbols_per_second",
        "credited_entropy_bps",
        "empirical_symbol_hmin_bps",
        "conditioned_output_bps",
        "conditioned_vs_credited_entropy",
        "time_to_target_seconds",
        "masked_throughput_vs_xor_lsb1",
        "conditioned_throughput_vs_xor_lsb1",
        "conditioned_written_bytes",
        "conditioned_input_bits_consumed",
        "conditioned_total_bytes",
        "conditioned_byte_min_entropy",
        "conditioned_p1",
        "conditioned_lag1",
        "symbol_min_entropy_bits_per_symbol",
        "symbol_min_entropy_bits_per_input_bit",
        "max_abs_cross_plane_phi",
        "max_cross_plane_mutual_information_bits",
        "bitplane_report",
        "state",
        "failure_reason",
        "health_sample_domain",
        "health_sample_width_bits",
        "health_rct_failures",
        "health_apt_failures",
        "health_rct_cutoff",
        "health_apt_cutoff",
        "profile_log",
        "failure_json",
        "report",
    ]
    if rows:
        with (root / "lsb_campaign_summary.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    table_keys = (
        "profile",
        "status",
        "failure_reason",
        "sample_mode",
        "lsb_bits",
        "entropy_credit_bits_per_pixel",
        "masked_input_bps",
        "masked_rate_basis",
        "pixel_symbols_per_second",
        "credited_entropy_bps",
        "empirical_symbol_hmin_bps",
        "conditioned_output_bps",
        "conditioned_vs_credited_entropy",
        "time_to_target_seconds",
        "masked_throughput_vs_xor_lsb1",
        "conditioned_throughput_vs_xor_lsb1",
        "symbol_min_entropy_bits_per_symbol",
        "symbol_min_entropy_bits_per_input_bit",
        "max_abs_cross_plane_phi",
        "max_cross_plane_mutual_information_bits",
    )
    table = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(format_cell(row.get(key)))}</td>" for key in table_keys
        )
        + (
            f'<td><a href="{html.escape(str(row["bitplane_report"]))}">LSB</a></td>'
            if row["bitplane_report"]
            else "<td>—</td>"
        )
        + (
            '<td>'
            + (f'<a href="{html.escape(str(row["report"]))}">raport</a> ' if row["report"] else '')
            + (f'<a href="{html.escape(str(row["failure_json"]))}">błąd</a> ' if row["failure_json"] else '')
            + (f'<a href="{html.escape(str(row["profile_log"]))}">log</a>' if row["profile_log"] else '')
            + ('—' if not row["report"] and not row["failure_json"] and not row["profile_log"] else '')
            + '</td>'
        )
        + "</tr>"
        for row in rows
    )
    document = f"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kampania LSB</title><style>:root{{color-scheme:dark;--bg:#0b0f14;--panel:#131a22;--border:#2a3441;--text:#edf4fb;--muted:#94a3b5;--accent:#55b5ff;--warn:#f8d477}}*{{box-sizing:border-box}}body{{font-family:system-ui;background:var(--bg);color:var(--text);margin:0;padding:24px}}a{{color:var(--accent)}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}}.card{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;overflow:auto}}.warn{{color:var(--warn)}}.muted{{color:var(--muted)}}td:nth-child(3){{white-space:normal;min-width:24rem}}</style></head><body><h1>Kampania LSB</h1><p>{html.escape(root.name)} · profile {len(rows)} · complete {complete} · failed {failed} · incomplete {incomplete}</p><p class="warn">Empiryczna przepustowość Hmin jest diagnostyką pojedynczego zbioru, a nie deklaracją SP 800-90B. „Credited entropy” wykorzystuje podany zewnętrznie budżet entropy credit.</p><p class="muted">Przepustowości względne są liczone względem profilu xor/1 LSB. Do porównań używane są przepustowości lifetime z tego samego okresu produkcyjnego. RCT/APT działa na k-bitowych symbolach pikseli; dopiero wejście conditionera jest serializowane pixel-major-lsb-first.</p><div class="card"><table><thead><tr><th>Profil</th><th>Status</th><th>Failure reason</th><th>Tryb</th><th>LSB</th><th>Credit/pixel</th><th>Masked bit/s</th><th>Rate basis</th><th>Pixel symbols/s</th><th>Credited entropy bit/s</th><th>Empirical Hmin bit/s</th><th>SHA3 bit/s</th><th>SHA3 / credited</th><th>Time to target [s]</th><th>Masked × baseline</th><th>SHA3 × baseline</th><th>Hmin symbol</th><th>Hmin/input bit</th><th>max |phi|</th><th>max MI</th><th>LSB</th><th>Raport</th></tr></thead><tbody>{table}</tbody></table></div><p><a href="lsb_campaign_summary.json">JSON</a> · <a href="lsb_campaign_summary.csv">CSV</a></p></body></html>"""
    (root / "lsb_campaign_report.html").write_text(document, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
