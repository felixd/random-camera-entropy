#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the comparison report for the exhaustive 1..4 LSB campaign.

The report keeps every production-relevant number in ordinary HTML tables and
in an embedded JSON bundle. Plotly charts are an additional comparison layer.
Throughput is stored internally as bit/s and can be displayed as bit/s, kbit/s,
kB/s, MiB/s or MB/s; kB/s is the default.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
import sys
from pathlib import Path
from typing import Any

from app.reporting.report_ui import (
    RawHtml,
    chart_div,
    esc,
    fmt,
    fmt_rate,
    fmt_percent,
    html_page,
    metric_card,
    metrics_grid,
    rate_span,
    rate_unit_selector,
    table_html,
)

APP_VERSION = "2026.08.05.camera-entropy-lsb-campaign.8.0.4"
MAX_LSB_BITS = 4


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
    for path in paths:
        value = finite_number(nested(record, *path))
        if value is not None and value > 0.0:
            return value, path[-1]
    return None, ""


def relevant_parameters(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source_type", "dataset_dir", "dataset_start_frame", "dataset_max_frames",
        "sample_mode", "lsb_bits", "entropy_credit_bits_per_pixel",
        "pairing_mode", "pair_lag_frames", "spatial_mask_pattern", "spatial_sampling",
        "spatial_step_x", "spatial_step_y", "spatial_phase_x", "spatial_phase_y",
        "spatial_block_width", "spatial_block_height", "temporal_spatial_offset_x",
        "temporal_spatial_offset_y", "serialization_order", "serialization_tile_width",
        "serialization_tile_height", "conditioner", "conditioner_input_bits",
        "minimum_conditioner_input_bits", "conditioned_output_bytes",
        "validation_output_bytes", "calibration_pairs", "thermal_warmup_seconds",
        "assessed_min_entropy", "apt_window", "health_alpha", "von_neumann_stage",
    )
    result: dict[str, Any] = {}
    for key in keys:
        if key in config:
            result[key] = config[key]
    for key in (
        "sample_mode", "lsb_bits", "entropy_credit_bits_per_pixel",
        "minimum_conditioner_input_bits", "conditioner_input_bits",
    ):
        if key in state:
            result[key] = state[key]
    return result


def profile_rows(root: Path) -> list[dict[str, Any]]:
    state_dir = root / "lsb_profiles"
    state_files = sorted(state_dir.glob("*.json")) if state_dir.is_dir() else []
    if state_files:
        sources = [(str((state := read_json(path)).get("profile") or path.stem), state) for path in state_files]
    else:
        sources = [(path.name, {}) for path in sorted(root.iterdir()) if path.is_dir() and path.name != "lsb_profiles"]

    rows: list[dict[str, Any]] = []
    for profile, state_record in sources:
        run = root / profile
        config = read_json(run / "runner_config.json")
        result = read_json(run / "runner_summary.json")
        complete_report = read_json(run / "output_complete.json")
        failed_report = read_json(run / "run_failed.json")
        terminal = complete_report or failed_report
        analysis = read_json(run / "analysis_conditioned" / "summary.json")
        bitplanes = read_json(run / "lsb_bitplane_summary.json")
        status = "failed" if failed_report else "complete" if complete_report else str(state_record.get("status") or "unknown")
        lsb_bits = int(state_record.get("lsb_bits", config.get("lsb_bits", 1)) or 1)
        credit = finite_number(state_record.get("entropy_credit_bits_per_pixel", config.get("entropy_credit_bits_per_pixel")))
        masked_bps, masked_basis = select_rate(terminal, ("rates", "masked_bps_lifetime"), ("rates", "masked_bps_10s"), ("rates", "masked_bps_current"))
        raw_bps, raw_basis = select_rate(terminal, ("rates", "raw_change_bps_lifetime"), ("rates", "raw_change_bps_10s"), ("rates", "raw_change_bps_current"))
        symbols_bps = masked_bps / lsb_bits if masked_bps is not None and lsb_bits > 0 else None
        symbol_hmin = finite_number(bitplanes.get("symbol_min_entropy_bits_per_symbol"))
        empirical_hmin_bps = symbols_bps * symbol_hmin if symbols_bps is not None and symbol_hmin is not None else None
        credited_bps = symbols_bps * credit if symbols_bps is not None and credit is not None else None
        conditioned_bps = finite_number(nested(terminal, "conditioner", "output_bps_until_complete"))
        utilization = conditioned_bps / credited_bps if conditioned_bps is not None and credited_bps and credited_bps > 0 else None
        health = terminal.get("health", {}) if isinstance(terminal.get("health"), dict) else {}
        parameters = relevant_parameters(config, state_record)
        rows.append({
            "profile": profile,
            "status": status,
            "exit_code": state_record.get("exit_code"),
            "sample_mode": state_record.get("sample_mode", config.get("sample_mode")),
            "lsb_bits": lsb_bits,
            "entropy_credit_bits_per_pixel": credit,
            "minimum_conditioner_input_bits": state_record.get("minimum_conditioner_input_bits", config.get("minimum_conditioner_input_bits")),
            "conditioner_input_bits": state_record.get("conditioner_input_bits", config.get("conditioner_input_bits")),
            "active_pixels": terminal.get("active_pixels"),
            "raw_input_bps": raw_bps,
            "masked_input_bps": masked_bps,
            "raw_rate_basis": raw_basis,
            "masked_rate_basis": masked_basis,
            "pixel_symbols_per_second": symbols_bps,
            "credited_entropy_bps": credited_bps,
            "empirical_symbol_hmin_bps": empirical_hmin_bps,
            "conditioned_output_bps": conditioned_bps,
            "conditioned_vs_credited_entropy": utilization,
            "time_to_target_seconds": finite_number(nested(terminal, "conditioner", "time_to_target_seconds")),
            "conditioned_written_bytes": nested(terminal, "conditioner", "written_bytes"),
            "conditioned_input_bits_consumed": nested(terminal, "conditioner", "input_bits_consumed"),
            "conditioned_total_bytes": analysis.get("total_bytes"),
            "conditioned_byte_min_entropy": analysis.get("byte_min_entropy_bits_per_byte"),
            "conditioned_p1": analysis.get("p1"),
            "conditioned_lag1": nested(analysis, "lag1", "phi"),
            "conditioned_chi_square_p_value": analysis.get("byte_chi_square_p_value"),
            "symbol_min_entropy_bits_per_symbol": symbol_hmin,
            "symbol_min_entropy_bits_per_input_bit": bitplanes.get("symbol_min_entropy_bits_per_input_bit"),
            "max_abs_cross_plane_phi": bitplanes.get("max_abs_cross_plane_phi"),
            "max_cross_plane_mutual_information_bits": bitplanes.get("max_cross_plane_mutual_information_bits"),
            "state": terminal.get("state") or terminal.get("status") or result.get("status"),
            "failure_reason": str(failed_report.get("reason") or ""),
            "health_sample_domain": health.get("sample_domain"),
            "health_sample_width_bits": health.get("sample_width_bits"),
            "health_rct_failures": health.get("rct_failures"),
            "health_apt_failures": health.get("apt_failures"),
            "health_rct_cutoff": health.get("rct_cutoff"),
            "health_apt_cutoff": health.get("apt_cutoff"),
            "parameters": parameters,
            "bitplane_report": f"{profile}/lsb_bitplane_report.html" if (run / "lsb_bitplane_report.html").exists() else "",
            "profile_log": f"{profile}.profile.log" if (root / f"{profile}.profile.log").exists() else "",
            "failure_json": f"{profile}/run_failed.json" if failed_report else "",
            "report": f"{profile}/run_report.html" if (run / "run_report.html").exists() else "",
        })

    baseline = next((row for row in rows if row.get("sample_mode") == "xor" and row.get("lsb_bits") == 1), None)
    baseline_masked = finite_number(baseline.get("masked_input_bps")) if baseline else None
    baseline_conditioned = finite_number(baseline.get("conditioned_output_bps")) if baseline else None
    for row in rows:
        masked = finite_number(row.get("masked_input_bps")); conditioned = finite_number(row.get("conditioned_output_bps"))
        row["masked_throughput_vs_xor_lsb1"] = masked / baseline_masked if masked is not None and baseline_masked else None
        row["conditioned_throughput_vs_xor_lsb1"] = conditioned / baseline_conditioned if conditioned is not None and baseline_conditioned else None
    return rows


def link_cell(row: dict[str, Any]) -> RawHtml:
    parts: list[str] = []
    for key, label in (("report", "raport"), ("bitplane_report", "LSB"), ("failure_json", "błąd"), ("profile_log", "log")):
        if row.get(key):
            parts.append(f'<a href="{esc(row[key])}">{label}</a>')
    return RawHtml(" · ".join(parts) if parts else "—")


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    campaign_config = read_json(root / "lsb_campaign_config.json")
    rows = profile_rows(root)
    complete = sum(row["status"] == "complete" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    incomplete = len(rows) - complete - failed
    summary = {
        "schema": "camera-entropy-lsb-campaign-v4",
        "app_version": APP_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign": root.name,
        "configuration": campaign_config,
        "max_lsb_bits": MAX_LSB_BITS,
        "default_rate_unit": "kB/s",
        "rate_units": ["bit/s", "kbit/s", "kB/s", "MiB/s", "MB/s"],
        "total_profiles": len(rows), "profiles": rows,
        "complete": complete, "failed": failed, "incomplete": incomplete,
        "warning": "Empirical entropy-rate values are single-dataset diagnostics, not an SP 800-90B min-entropy claim. Credited entropy rate is an externally supplied budget.",
    }
    (root / "lsb_campaign_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    scalar_fields = [key for key in rows[0].keys() if key != "parameters"] if rows else ["profile"]
    with (root / "lsb_campaign_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=scalar_fields + ["parameters_json"], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "parameters_json": json.dumps(row.get("parameters", {}), ensure_ascii=False, sort_keys=True)})

    labels = [str(row["profile"]) for row in rows]
    plot_specs = [
        {
            "id": "rates-all",
            "rateAxis": "y", "rateTitle": "Przepustowość",
            "data": [
                {"type": "bar", "name": "Wejście maskowane", "x": labels, "y": [row.get("masked_input_bps") for row in rows]},
                {"type": "bar", "name": "Credited entropy", "x": labels, "y": [row.get("credited_entropy_bps") for row in rows]},
                {"type": "bar", "name": "Empiryczne Hmin", "x": labels, "y": [row.get("empirical_symbol_hmin_bps") for row in rows]},
                {"type": "bar", "name": "SHA3-512", "x": labels, "y": [row.get("conditioned_output_bps") for row in rows]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Profil", "tickangle": -30}, "yaxis": {"rangemode": "tozero"}},
        },
        {
            "id": "hmin",
            "data": [
                {"type": "bar", "name": "Hmin / symbol", "x": labels, "y": [row.get("symbol_min_entropy_bits_per_symbol") for row in rows]},
                {"type": "bar", "name": "Hmin / pobrany bit", "x": labels, "y": [row.get("symbol_min_entropy_bits_per_input_bit") for row in rows]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Profil", "tickangle": -30}, "yaxis": {"title": "bit", "rangemode": "tozero"}},
        },
        {
            "id": "dependencies",
            "data": [
                {"type": "bar", "name": "max |φ| między płaszczyznami", "x": labels, "y": [row.get("max_abs_cross_plane_phi") for row in rows]},
                {"type": "bar", "name": "max MI [bit]", "x": labels, "y": [row.get("max_cross_plane_mutual_information_bits") for row in rows]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Profil", "tickangle": -30}, "yaxis": {"title": "zależność", "rangemode": "tozero"}},
        },
        {
            "id": "output-quality",
            "data": [
                {"type": "scatter", "mode": "lines+markers", "name": "|P(1)-0.5|", "x": labels, "y": [abs(float(row["conditioned_p1"]) - .5) if finite_number(row.get("conditioned_p1")) is not None else None for row in rows]},
                {"type": "scatter", "mode": "lines+markers", "name": "|φ lag-1|", "x": labels, "y": [abs(float(row["conditioned_lag1"])) if finite_number(row.get("conditioned_lag1")) is not None else None for row in rows]},
            ],
            "layout": {"xaxis": {"title": "Profil", "tickangle": -30}, "yaxis": {"title": "odchylenie bezwzględne", "rangemode": "tozero"}},
        },
        {
            "id": "time-and-utilization",
            "data": [
                {"type": "bar", "name": "Czas do celu [s]", "x": labels, "y": [row.get("time_to_target_seconds") for row in rows]},
                {"type": "bar", "name": "SHA3 / credited", "x": labels, "y": [row.get("conditioned_vs_credited_entropy") for row in rows], "yaxis": "y2"},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Profil", "tickangle": -30}, "yaxis": {"title": "sekundy", "rangemode": "tozero"}, "yaxis2": {"title": "udział", "overlaying": "y", "side": "right", "rangemode": "tozero"}},
        },
    ]

    best_hmin = max((row for row in rows if finite_number(row.get("symbol_min_entropy_bits_per_input_bit")) is not None), key=lambda row: float(row["symbol_min_entropy_bits_per_input_bit"]), default=None)
    fastest = max((row for row in rows if finite_number(row.get("conditioned_output_bps")) is not None), key=lambda row: float(row["conditioned_output_bps"]), default=None)
    cards = [
        metric_card("Profile", len(rows), f"complete {complete} · failed {failed} · incomplete {incomplete}", "good" if failed == 0 and incomplete == 0 else "warn"),
        metric_card("Zakres LSB", "1–4", "pełna macierz direct / xor / delta"),
        metric_card("Najwyższe Hmin / bit", best_hmin.get("profile") if best_hmin else "—", fmt(best_hmin.get("symbol_min_entropy_bits_per_input_bit"), 7) if best_hmin else "—"),
        metric_card("Najszybszy SHA3", fastest.get("profile") if fastest else "—", rate_span(fastest.get("conditioned_output_bps")) if fastest else "—"),
    ]

    main_table = table_html(
        ["Profil", "Status", "Tryb", "LSB", "Credit/pixel", "Masked", "Credited", "Empiryczne Hmin", "SHA3", "Hmin/symbol", "Hmin/bit", "max |φ|", "max MI", "Czas [s]", "Health", "Linki"],
        [[
            row["profile"], row["status"], row.get("sample_mode"), row.get("lsb_bits"), fmt(row.get("entropy_credit_bits_per_pixel"), 6),
            rate_span(row.get("masked_input_bps")), rate_span(row.get("credited_entropy_bps")), rate_span(row.get("empirical_symbol_hmin_bps")), rate_span(row.get("conditioned_output_bps")),
            fmt(row.get("symbol_min_entropy_bits_per_symbol"), 7), fmt(row.get("symbol_min_entropy_bits_per_input_bit"), 7), fmt(row.get("max_abs_cross_plane_phi"), 7), fmt(row.get("max_cross_plane_mutual_information_bits"), 7), fmt(row.get("time_to_target_seconds"), 6),
            f"RCT {row.get('health_rct_failures') or 0} / APT {row.get('health_apt_failures') or 0}", link_cell(row),
        ] for row in rows], compact=True,
    )

    parameter_sections = "".join(
        '<details><summary>' + esc(row["profile"]) + ' — dokładne parametry</summary>'
        + table_html(["Parametr", "Wartość"], [[key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value] for key, value in sorted(row.get("parameters", {}).items())], compact=True)
        + (f'<p class="callout bad"><strong>Błąd:</strong> {esc(row["failure_reason"])}</p>' if row.get("failure_reason") else "")
        + '</details>'
        for row in rows
    )

    campaign_config_table = table_html(
        ["Parametr kampanii", "Wartość"],
        [[key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value] for key, value in sorted(campaign_config.items())],
        compact=True,
    ) if campaign_config else "<p>Brak osobnego pliku konfiguracji kampanii; parametry są dostępne przy każdym profilu.</p>"

    body = (
        '<div class="callout warn"><strong>Zakres:</strong> raport porównuje wszystkie tryby próbkowania dla 1–4 LSB. Empiryczne Hmin nie jest formalną deklaracją SP 800-90B.</div>'
        + '<section><h2>Konfiguracja kampanii</h2>' + campaign_config_table + '</section>'
        + metrics_grid(cards)
        + '<div class="chart-grid">'
        + chart_div("rates-all", "Przepustowość wszystkich etapów", "Wartości z tabeli na wspólnej skali; jednostkę można zmienić u góry strony.", 440)
        + chart_div("hmin", "Min-entropia symbolu i pobranego bitu", "Porównanie wartości całego symbolu k-bitowego z efektywnością na wejściowy bit.", 440)
        + '</div><div class="chart-grid">'
        + chart_div("dependencies", "Zależności między płaszczyznami bitowymi", "Niższe wartości φ i mutual information są lepsze.", 390)
        + chart_div("output-quality", "Jakość finalnego SHA3-512", "Odchylenie P(1) oraz korelacja lag-1 po conditionerze.", 390)
        + '</div>'
        + chart_div("time-and-utilization", "Czas i wykorzystanie budżetu credited entropy", "Czas osiągnięcia celu i relacja finalnego strumienia do przypisanego budżetu.", 380)
        + '<section><h2>Pełna tabela porównawcza</h2>' + main_table + '</section>'
        + '<section><h2>Parametry każdego testu</h2><p class="muted">Każdy profil pokazuje parametry od źródła aż do conditionera.</p>' + parameter_sections + '</section>'
        + '<section><h2>Dane maszynowe</h2><p>Pełny JSON jest osadzony w tym pliku i dostępny również osobno jako <a href="lsb_campaign_summary.json">lsb_campaign_summary.json</a>.</p></section>'
        + '<script type="application/json" id="lsb-campaign-data">' + json.dumps(summary, ensure_ascii=False).replace("</", "<\\/") + '</script>'
    )
    navigation = rate_unit_selector() + '<a href="lsb_campaign_summary.json">JSON</a><a href="lsb_campaign_summary.csv">CSV</a>'
    document = html_page(title="Kampania LSB 1–4", subtitle=f"{root.name} · profile {len(rows)}", navigation=navigation, body=body, plot_specs=plot_specs)
    (root / "lsb_campaign_report.html").write_text(document, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
