#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final, targeted pre-production qualification over the whole buffered dataset.

The profile is intentionally smaller than the exploratory production matrix. It
runs the known leading candidate, its throughput optimisation, a lag-2 variant,
a two-bit challenger and a direct-LSB control. Every case consumes the same
snapshot of all currently committed dataset frames. Cases may execute in
parallel because dataset-y is read-only.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
from app.paths import PROJECT_ROOT
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from app.reporting.report_ui import RawHtml, chart_div, esc, fmt, html_page, metric_card, metrics_grid, rate_span, rate_unit_selector, table_html
from app.reporting.summarize_production_assessment import collect_case, finite, healthy
from app.sources.dataset_integrity import default_verification_cache_path, verify_dataset_chunks

APP_VERSION = "2026.08.05.camera-entropy-final-preproduction.8.0.0"
ROOT = PROJECT_ROOT
_STOP = threading.Event()
_CHILDREN: dict[str, subprocess.Popen[str]] = {}
_CHILD_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def dataset_snapshot(raw: str | None = None) -> tuple[Path, dict[str, Any]]:
    raw = raw or os.environ.get("DATASET_DIR", "data/frame-buffer-latest")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    manifest = read_json(path / "manifest.json")
    if manifest.get("format") != "camera-entropy-frame-buffer-v1":
        raise SystemExit(f"Nieprawidłowy dataset: {path}")
    status = str(manifest.get("status") or "").strip().lower()
    if status not in {"recording", "stopped", "complete"}:
        raise SystemExit(f"Dataset ma nieczytelny status: {status or 'missing'}")
    frame_count = int(manifest.get("frame_count", 0) or 0)
    if frame_count < 1024:
        raise SystemExit(f"Dataset ma za mało klatek do kwalifikacji: {frame_count}")
    if not (path / "frames.csv").is_file() or not (path / "chunks").is_dir():
        raise SystemExit("Dataset nie zawiera frames.csv lub chunks/")
    return path, manifest


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _human_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(max(0.0, value))
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024.0
    return f"{amount:.2f} TiB"


def _human_duration(seconds: float | None) -> str:
    if seconds is None or not math_isfinite(seconds):
        return "—"
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


def math_isfinite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {float("inf"), float("-inf")}


def _positive_int(value: int, name: str, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} musi być w zakresie {minimum}..{maximum}")
    return value


def parse_args() -> argparse.Namespace:
    cpu_count = max(1, os.cpu_count() or 1)
    default_case_workers = min(5, max(1, cpu_count // 2))
    default_verify_workers = min(8, max(2, cpu_count // 4))
    parser = argparse.ArgumentParser(
        description="Ostateczna kwalifikacja przedprodukcyjna na pełnym datasecie Y8/LSB.",
    )
    parser.add_argument(
        "--dataset-dir",
        default=os.environ.get("DATASET_DIR", "data/frame-buffer-latest"),
        help="Dataset-y; domyślnie DATASET_DIR lub data/frame-buffer-latest.",
    )
    parser.add_argument(
        "--workers", type=int,
        default=int(os.environ.get("FINAL_PREPROD_WORKERS", default_case_workers)),
        help="Liczba równoległych wariantów analizy (1..5).",
    )
    parser.add_argument(
        "--verify-workers", type=int,
        default=int(os.environ.get("FINAL_PREPROD_VERIFY_WORKERS", os.environ.get("DATASET_VERIFY_WORKERS", default_verify_workers))),
        help="Liczba równoległych workerów SHA-256 (1..64). Dla HDD zwykle 1–2; dla NVMe 4–16.",
    )
    parser.add_argument(
        "--verify-hashes", action=argparse.BooleanOptionalAction,
        default=_env_bool("FINAL_PREPROD_VERIFY_HASHES", _env_bool("DATASET_VERIFY_HASHES", True)),
        help="Weryfikuj SHA-256 wszystkich zamkniętych chunków przed analizą.",
    )
    parser.add_argument(
        "--verify-cache", action=argparse.BooleanOptionalAction,
        default=_env_bool("FINAL_PREPROD_VERIFY_CACHE", True),
        help="Ponownie użyj wyniku wcześniejszej pełnej weryfikacji, jeżeli manifest i metadane chunków są identyczne.",
    )
    parser.add_argument(
        "--progress-interval", type=float,
        default=float(os.environ.get("FINAL_PREPROD_PROGRESS_INTERVAL_SECONDS", "2")),
        help="Co ile sekund wypisywać postęp weryfikacji.",
    )
    parser.add_argument(
        "--status-interval", type=float,
        default=float(os.environ.get("FINAL_PREPROD_STATUS_INTERVAL_SECONDS", "30")),
        help="Co ile sekund wypisywać heartbeat trwających analiz.",
    )
    parser.add_argument("--campaign", default=os.environ.get("CAMPAIGN"))
    args = parser.parse_args()
    args.workers = _positive_int(args.workers, "--workers", 1, 5)
    args.verify_workers = _positive_int(args.verify_workers, "--verify-workers", 1, 64)
    if args.progress_interval <= 0:
        parser.error("--progress-interval musi być dodatni")
    if args.status_interval <= 0:
        parser.error("--status-interval musi być dodatni")
    return args


def cases() -> list[dict[str, Any]]:
    common = {
        "PAIRING_MODE": "disjoint", "SPATIAL_MASK_PATTERN": "full", "SPATIAL_SAMPLING": "full",
        "SERIALIZATION_ORDER": "row-major", "VON_NEUMANN_PASSES": 0, "ENABLE_VON_NEUMANN": 0,
        "CONDITIONER": "sha3-512", "ENTROPY_CREDIT_BITS_PER_PIXEL": 0.5,
    }
    return [
        {"id": "xor1-safe-k4-2048", "role": "recommended", "label": "XOR 1 LSB · k=4 · SHA3 input 2048 (bezpieczny)", "parameters": common | {"SAMPLE_MODE": "xor", "LSB_BITS": 1, "PAIR_LAG_FRAMES": 4, "CONDITIONER_INPUT_BITS": 2048}},
        {"id": "xor1-fast-k4-1024", "role": "optimisation", "label": "XOR 1 LSB · k=4 · SHA3 input 1024 (wydajnościowy)", "parameters": common | {"SAMPLE_MODE": "xor", "LSB_BITS": 1, "PAIR_LAG_FRAMES": 4, "CONDITIONER_INPUT_BITS": 1024}},
        {"id": "xor1-fast-k2-1024", "role": "lag-challenger", "label": "XOR 1 LSB · k=2 · SHA3 input 1024", "parameters": common | {"SAMPLE_MODE": "xor", "LSB_BITS": 1, "PAIR_LAG_FRAMES": 2, "CONDITIONER_INPUT_BITS": 1024}},
        {"id": "xor2-k4-1024", "role": "width-challenger", "label": "XOR 2 LSB · k=4 · credit 1.0 · SHA3 input 1024", "parameters": common | {"SAMPLE_MODE": "xor", "LSB_BITS": 2, "PAIR_LAG_FRAMES": 4, "ENTROPY_CREDIT_BITS_PER_PIXEL": 1.0, "CONDITIONER_INPUT_BITS": 1024}},
        {"id": "direct1-control-k4-2048", "role": "control", "label": "Direct Y 1 LSB · kontrola · SHA3 input 2048", "parameters": common | {"SAMPLE_MODE": "direct", "LSB_BITS": 1, "PAIR_LAG_FRAMES": 4, "CONDITIONER_INPUT_BITS": 2048}},
    ]


def terminate_all() -> None:
    with _CHILD_LOCK:
        children = list(_CHILDREN.values())
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def run_case(index: int, case: dict[str, Any], campaign_dir: Path, base_env: dict[str, str], port: int) -> dict[str, Any]:
    ident = str(case["id"])
    state_path = campaign_dir / "cases" / f"{index:02d}-{ident}.json"
    run_dir = campaign_dir / "runs" / ident
    log_path = campaign_dir / "logs" / f"{ident}.log"
    state = {
        **case, "index": index, "stage": "final-preproduction", "status": "running",
        "started_utc": utc_now(), "run_dir": f"runs/{ident}", "log": f"logs/{ident}.log",
    }
    atomic_json(state_path, state)
    print(f"[final-preproduction] START {index:02d}/{len(cases())}: {ident} · port={port}", flush=True)
    environment = base_env | {key: str(value) for key, value in case["parameters"].items()} | {
        "PORT": str(port), "RUN_NAME": f"runs/{ident}", "RUN_DIR": str(run_dir),
    }
    with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
        child = subprocess.Popen(
            [str(ROOT / "scripts" / "run" / "run_one.sh")], cwd=ROOT, env=environment,
            stdout=log_stream, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        with _CHILD_LOCK:
            _CHILDREN[ident] = child
        returncode = child.wait()
        with _CHILD_LOCK:
            _CHILDREN.pop(ident, None)
    state.update({"status": "complete" if returncode == 0 else "failed", "exit_code": returncode, "ended_utc": utc_now()})
    atomic_json(state_path, state)
    print(f"[final-preproduction] END   {index:02d}/{len(cases())}: {ident} · {state['status']} · rc={returncode}", flush=True)
    return state


def gate(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not healthy(row): reasons.append("health/status")
    if row.get("completion_reason") != "dataset-exhausted": reasons.append("nie wykorzystano całego snapshotu datasetu")
    if row.get("targets_complete") is not True: reasons.append("nie osiągnięto wymaganych limitów plików wyjściowych")
    if finite(row.get("hmin_per_input_bit")) is None or float(row["hmin_per_input_bit"]) < 0.98: reasons.append("Hmin/input bit < 0.98")
    if finite(row.get("worst_window_hmin_per_input_bit")) is None or float(row["worst_window_hmin_per_input_bit"]) < 0.98: reasons.append("najgorsze okno Hmin/input bit < 0.98")
    if finite(row.get("output_byte_hmin")) is None or float(row["output_byte_hmin"]) < 7.90: reasons.append("SHA3 Hmin/byte < 7.90")
    if finite(row.get("output_p1_deviation")) is None or float(row["output_p1_deviation"]) > 0.001: reasons.append("|P(1)-0.5| > 0.001")
    if finite(row.get("output_abs_lag1")) is None or float(row["output_abs_lag1"]) > 0.005: reasons.append("|lag1| > 0.005")
    if finite(row.get("output_chi_square_p")) is None or not 0.0001 <= float(row["output_chi_square_p"]) <= 0.9999: reasons.append("χ² p poza [0.0001,0.9999]")
    return not reasons, reasons


def render_report(campaign_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    state_paths = sorted((campaign_dir / "cases").glob("*.json"))
    rows = [collect_case(campaign_dir, path) for path in state_paths]
    metadata = {str(item.get("id")): item for item in (read_json(path) for path in state_paths)}
    for row in rows:
        meta = metadata.get(str(row.get("id")), {})
        row["role"] = meta.get("role")
        run = campaign_dir / str(row.get("run_dir", ""))
        completion = read_json(run / "output_complete.json")
        row["completion_reason"] = completion.get("completion_reason")
        row["targets_complete"] = completion.get("targets_complete")
        row["source_frames"] = completion.get("frames") or completion.get("source_frames")
        passed, reasons = gate(row) if row.get("role") != "control" else (healthy(row), [])
        row["gate_passed"] = passed
        row["gate_reasons"] = reasons
    by_id = {str(row.get("id")): row for row in rows}
    safe = by_id.get("xor1-safe-k4-2048", {})
    fast = by_id.get("xor1-fast-k4-1024", {})
    safe_ok = bool(safe.get("gate_passed"))
    fast_ok = bool(fast.get("gate_passed"))
    decision = {
        "production_candidate": "xor1-safe-k4-2048" if safe_ok else None,
        "optimisation_candidate": "xor1-fast-k4-1024" if safe_ok and fast_ok else None,
        "production_ready": safe_ok,
        "summary": (
            "Zatwierdzić bezpieczny XOR/1 LSB/k=4/credit 0.5/SHA3-512 input 2048. "
            + ("Wariant 1024 przeszedł bramki i może wejść do kontrolowanego A/B." if fast_ok else "Wariant 1024 nie przeszedł wszystkich bramek.")
            if safe_ok else "Nie zatwierdzać: bezpieczny kandydat nie przeszedł wszystkich bramek."
        ),
    }
    summary = {
        "schema": "camera-entropy-final-preproduction-summary-v1", "app_version": APP_VERSION,
        "generated_utc": utc_now(), "config": config, "decision": decision, "cases": rows,
    }
    atomic_json(campaign_dir / "final_preproduction_summary.json", summary)

    labels = [str(row.get("label")) for row in rows]
    plot_specs = [
        {"id": "rates", "rate": True, "data": [
            {"type": "bar", "name": "Masked input", "x": labels, "y": [row.get("masked_input_bps") for row in rows]},
            {"type": "bar", "name": "SHA3 output", "x": labels, "y": [row.get("conditioned_output_bps") for row in rows]},
        ], "layout": {"barmode": "group", "xaxis": {"tickangle": -25}, "yaxis": {"title": "kB/s"}}},
        {"id": "hmin", "data": [
            {"type": "bar", "name": "Hmin/input bit", "x": labels, "y": [row.get("hmin_per_input_bit") for row in rows]},
            {"type": "bar", "name": "SHA3 Hmin/byte ÷ 8", "x": labels, "y": [(finite(row.get("output_byte_hmin")) or 0)/8 for row in rows]},
        ], "layout": {"barmode": "group", "xaxis": {"tickangle": -25}, "yaxis": {"range": [0, 1.02]}}},
        {"id": "quality", "data": [
            {"type": "bar", "name": "|P(1)-0.5|", "x": labels, "y": [row.get("output_p1_deviation") for row in rows]},
            {"type": "bar", "name": "|lag-1|", "x": labels, "y": [row.get("output_abs_lag1") for row in rows]},
        ], "layout": {"barmode": "group", "xaxis": {"tickangle": -25}, "yaxis": {"rangemode": "tozero"}}},
        {"id": "window-hmin", "data": [
            {
                "type": "scatter", "mode": "lines+markers", "name": str(row.get("label")),
                "x": [window.get("index") for window in (row.get("stream_windows") or [])],
                "y": [window.get("symbol_min_entropy_bits_per_input_bit") for window in (row.get("stream_windows") or [])],
            }
            for row in rows if row.get("stream_windows")
        ], "layout": {"xaxis": {"title": "Kolejne okno całego datasetu"}, "yaxis": {"title": "Hmin / input bit", "range": [0.9, 1.001]} }},
    ]
    links = lambda row: RawHtml(" · ".join(filter(None, [
        f'<a href="{esc(row.get("run_dir"))}/run_report.html">raport</a>' if row.get("report") else "",
        f'<a href="{esc(row.get("run_dir"))}/lsb_bitplane_report.html">LSB plik</a>' if row.get("bitplane_report") else "",
        f'<a href="{esc(row.get("stream_lsb_json"))}">LSB cały dataset</a>' if row.get("stream_lsb_json") else "",
        f'<a href="{esc(row.get("log"))}">log</a>',
    ])))
    table = table_html(
        ["Wariant", "Rola", "Status", "Pełny dataset", "Cele plików", "Hmin/bit cały", "Najgorsze okno", "Okna", "SHA3 Hmin/B", "|bias|", "|lag1|", "χ² p", "SHA3", "Bramka", "Pliki"],
        [[
            row.get("label"), row.get("role"), row.get("status"),
            "TAK" if row.get("completion_reason") == "dataset-exhausted" else "NIE",
            "TAK" if row.get("targets_complete") is True else "NIE",
            fmt(row.get("hmin_per_input_bit"), 7),
            fmt(row.get("worst_window_hmin_per_input_bit"), 7), row.get("stream_window_count"),
            fmt(row.get("output_byte_hmin"), 7), fmt(row.get("output_p1_deviation"), 7),
            fmt(row.get("output_abs_lag1"), 7), fmt(row.get("output_chi_square_p"), 6),
            rate_span(row.get("conditioned_output_bps")),
            "PASS" if row.get("gate_passed") else "FAIL: " + ", ".join(row.get("gate_reasons") or []), links(row),
        ] for row in rows], compact=True,
    )
    params = "".join(
        f'<details><summary>{esc(row.get("label"))}</summary><pre>{esc(json.dumps(row.get("parameters", {}), ensure_ascii=False, indent=2))}</pre></details>'
        for row in rows
    )
    verification = config.get("verification", {})
    body = (
        metrics_grid([
            metric_card("Decyzja", "PASS" if decision["production_ready"] else "STOP", decision["summary"], "good" if decision["production_ready"] else "bad"),
            metric_card("Dataset", f'{config["dataset"]["frame_count"]:,} klatek', f'{config["dataset"]["bytes_written"]:,} B · snapshot', "good"),
            metric_card("Integralność", "VERIFIED" if verification.get("verified") else "NOT VERIFIED", f'{verification.get("closed_chunks", 0)} zamkniętych chunków', "good" if verification.get("verified") else "warn"),
            metric_card("Przypadki", f'{sum(1 for r in rows if r.get("status")=="complete")}/{len(rows)}', "pełny dataset na każdy wariant", "good"),
        ])
        + '<section><h2>Werdykt automatyczny</h2><p class="callout">' + esc(decision["summary"]) + '</p></section>'
        + rate_unit_selector(selected="kB/s")
        + chart_div("rates", "Przepustowość wejścia i finalnego SHA3", "Jednostkę można zmienić nad wykresami.", 430)
        + chart_div("hmin", "Min-entropia wejścia i diagnostyka wyjścia", "Hmin finalnego pliku nie zastępuje oceny źródła.", 390)
        + chart_div("quality", "Bias i korelacja finalnego SHA3", "Niżej jest lepiej.", 390)
        + chart_div("window-hmin", "Stabilność Hmin na całym datasecie", "Każdy punkt obejmuje kolejne okno par ramek; bramka używa najgorszego okna.", 430)
        + '<section><h2>Wszystkie wyniki</h2>' + table + '</section>'
        + '<section><h2>Dokładne parametry każdego testu</h2>' + params + '</section>'
        + '<section><h2>Konfiguracja kampanii</h2><pre>' + esc(json.dumps(config, ensure_ascii=False, indent=2)) + '</pre></section>'
        + '<script type="application/json" id="final-preproduction-data">' + json.dumps(summary, ensure_ascii=False).replace('</', '<\\/') + '</script>'
    )
    html = html_page(
        title="Camera Entropy — ostateczna kwalifikacja przedprodukcyjna",
        subtitle="Celowany test całego dostępnego datasetu; wyniki, parametry i decyzja w jednym pliku.",
        navigation='<a href="final_preproduction_summary.json">JSON</a>', body=body,
        plot_specs=plot_specs, standalone=True,
    )
    (campaign_dir / "final_preproduction_report.html").write_text(html, encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    dataset, manifest = dataset_snapshot(args.dataset_dir)
    data_root = Path(os.environ.get("DATA_ROOT", ROOT / "data")).expanduser().resolve()
    campaign = args.campaign or f"final-preproduction-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    campaign_dir = data_root / campaign
    if campaign_dir.exists():
        raise SystemExit(f"Katalog kampanii istnieje: {campaign_dir}")
    for sub in ("runs", "cases", "logs"):
        (campaign_dir / sub).mkdir(parents=True, exist_ok=True)

    def handle_signal(_signum: int, _frame: Any) -> None:
        _STOP.set()
        terminate_all()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    frame_count = int(manifest.get("frame_count", 0) or 0)
    dataset_bytes = int(manifest.get("bytes_written", 0) or 0)
    candidate_cases = cases()
    workers = max(1, min(len(candidate_cases), args.workers))
    print(f"[final-preproduction] {APP_VERSION}", flush=True)
    print(f"[final-preproduction] Dataset: {dataset}", flush=True)
    print(
        f"[final-preproduction] Snapshot: {frame_count:,} klatek · {_human_bytes(dataset_bytes)} · "
        f"status={manifest.get('status')}",
        flush=True,
    )
    print(
        f"[final-preproduction] Workery analizy: {workers}/{len(candidate_cases)} · "
        f"SHA-256: {'ON' if args.verify_hashes else 'OFF'} · workery weryfikacji: {args.verify_workers}",
        flush=True,
    )


    verification_progress_path = campaign_dir / "verification_progress.json"
    last_progress_line = {"text": ""}

    def verification_progress(progress: dict[str, Any]) -> None:
        payload = dict(progress) | {"updated_utc": utc_now(), "dataset": str(dataset)}
        atomic_json(verification_progress_path, payload)
        phase = str(progress.get("phase", "verifying"))
        if phase == "cache-hit":
            text = (
                f"[final-preproduction] SHA-256: CACHE HIT · "
                f"{progress.get('closed_chunks', 0)} chunków · {_human_bytes(progress.get('total_bytes', 0))}"
            )
        else:
            fraction = float(progress.get("fraction", 0.0) or 0.0)
            text = (
                f"[final-preproduction] SHA-256 {100.0*fraction:6.2f}% · "
                f"{progress.get('completed_chunks', 0)}/{progress.get('closed_chunks', 0)} chunków · "
                f"{_human_bytes(progress.get('bytes_read', 0))}/{_human_bytes(progress.get('total_bytes', 0))} · "
                f"{_human_bytes(progress.get('bytes_per_second', 0))}/s · ETA {_human_duration(progress.get('eta_seconds'))}"
            )
        if text != last_progress_line["text"]:
            print(text, flush=True)
            last_progress_line["text"] = text

    verification: dict[str, Any]
    if args.verify_hashes:
        raw_cache_root = os.environ.get("DATASET_VERIFY_CACHE_DIR", "").strip()
        cache_root = Path(raw_cache_root) if raw_cache_root else None
        cache_path = default_verification_cache_path(dataset, cache_root)
        print(
            f"[final-preproduction] Rozpoczynam weryfikację zamkniętych chunków "
            f"({args.verify_workers} workerów; cache={'ON' if args.verify_cache else 'OFF'}).",
            flush=True,
        )
        try:
            verification = verify_dataset_chunks(
                dataset,
                workers=args.verify_workers,
                progress_interval_seconds=args.progress_interval,
                progress_callback=verification_progress,
                stop_event=_STOP,
                allow_incomplete_last_line=str(manifest.get("status")) == "recording",
                cache_path=cache_path,
                use_cache=args.verify_cache and str(manifest.get("status")) != "recording",
            )
        except (InterruptedError, KeyboardInterrupt):
            result = {"status": "stopped", "stage": "dataset-verification", "ended_utc": utc_now()}
            atomic_json(campaign_dir / "run_failed.json", result)
            print("[final-preproduction] Weryfikacja przerwana przez operatora.", file=sys.stderr, flush=True)
            return 130
        except Exception as exc:
            result = {"status": "failed", "stage": "dataset-verification", "error": f"{type(exc).__name__}: {exc}", "ended_utc": utc_now()}
            atomic_json(campaign_dir / "run_failed.json", result)
            print(f"[final-preproduction] Weryfikacja NIEUDANA: {result['error']}", file=sys.stderr, flush=True)
            return 1
        print(
            f"[final-preproduction] Weryfikacja zakończona: {verification.get('closed_chunks', 0)} chunków · "
            f"{_human_bytes(verification.get('bytes', 0))} · {_human_duration(verification.get('elapsed_seconds'))}"
            + (" · wynik z cache" if verification.get("cached") else ""),
            flush=True,
        )
    else:
        verification = {"verified": False, "reason": "disabled", "workers": 0}
        atomic_json(verification_progress_path, verification | {"updated_utc": utc_now()})

    if _STOP.is_set():
        return 130

    base_port = int(os.environ.get("WORKER_PORT_BASE", os.environ.get("PORT", "18087")))
    common = {
        "DATA_ROOT": str(campaign_dir), "SOURCE_TYPE": "dataset-y", "DATASET_DIR": str(dataset),
        "DATASET_START_FRAME": "0", "DATASET_MAX_FRAMES": str(frame_count), "DATASET_FOLLOW": "0",
        "DATASET_REALTIME": "0", "DATASET_VERIFY_HASHES": "0", "EXIT_ON_OUTPUT_LIMIT": "0",
        "WARMUP_SECONDS": "0", "CALIBRATION_PAIRS": os.environ.get("CALIBRATION_PAIRS", "512"),
        "CONDITIONED_BYTES": os.environ.get("CONDITIONED_BYTES", str(64*1024*1024)),
        "VALIDATION_BYTES": os.environ.get("VALIDATION_BYTES", str(64*1024*1024)),
        "DIAGNOSTIC_VN_BYTES": "0", "WEB_IMAGES": "0", "MASK_SNAPSHOT_IMAGES": "0",
        "LIVE_BYTE_DIAGNOSTICS": "0", "LIVE_HEATMAP_INTERVAL_SECONDS": "0", "BINARY_GEOMETRY_REPORT": "0",
        "CORRELATION_EVERY": os.environ.get("CORRELATION_EVERY", "50"),
        "STREAM_STATS_WINDOW_PAIRS": os.environ.get("STREAM_STATS_WINDOW_PAIRS", "1024"),
    }
    config = {
        "schema": "camera-entropy-final-preproduction-config-v2", "app_version": APP_VERSION,
        "created_utc": utc_now(), "campaign": campaign, "parallel_workers": workers,
        "verification_workers": args.verify_workers, "verification_cache": args.verify_cache,
        "progress_interval_seconds": args.progress_interval, "status_interval_seconds": args.status_interval,
        "dataset": {"path": str(dataset), "status": manifest.get("status"), "storage_mode": manifest.get("storage_mode"),
                    "width": manifest.get("width"), "height": manifest.get("height"), "frame_count": frame_count,
                    "bytes_written": dataset_bytes, "snapshot_utc": utc_now()},
        "verification": verification, "common_environment": common, "cases": candidate_cases,
        "decision_gates": {"dataset_completion_reason": "dataset-exhausted", "targets_complete": True,
                           "hmin_per_input_bit_min": 0.98, "worst_window_hmin_per_input_bit_min": 0.98, "output_byte_hmin_min": 7.90,
                           "output_bias_max": 0.001, "output_abs_lag1_max": 0.005,
                           "chi_square_p_range": [0.0001, 0.9999], "health_failures": 0},
    }
    atomic_json(campaign_dir / "final_preproduction_config.json", config)

    print(
        f"[final-preproduction] Start analizy: {len(candidate_cases)} wariantów, "
        f"maksymalnie {workers} równolegle. Logi wariantów: {campaign_dir / 'logs'}",
        flush=True,
    )
    base_env = dict(os.environ) | common
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="preprod") as pool:
        pending = {
            pool.submit(run_case, index, case, campaign_dir, base_env, base_port + index - 1): case
            for index, case in enumerate(candidate_cases, 1)
        }
        started_monotonic = time.monotonic()
        while pending:
            done, not_done = wait(set(pending), timeout=args.status_interval, return_when=FIRST_COMPLETED)
            if not done:
                running_ids = ", ".join(str(pending[future]["id"]) for future in not_done)
                print(
                    f"[final-preproduction] HEARTBEAT · zakończone {len(results)}/{len(candidate_cases)} · "
                    f"trwają {len(not_done)} · elapsed {_human_duration(time.monotonic()-started_monotonic)} · {running_ids}",
                    flush=True,
                )
                continue
            for future in done:
                case = pending.pop(future)
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    _STOP.set(); terminate_all()
                    print(f"[final-preproduction] {case['id']}: exception: {exc}", file=sys.stderr, flush=True)
                    results.append({"id": case["id"], "status": "failed", "error": str(exc)})

    print("[final-preproduction] Wszystkie warianty zakończone. Generuję raport zbiorczy…", flush=True)
    summary = render_report(campaign_dir, config)
    failed = (
        _STOP.is_set()
        or any(item.get("status") != "complete" for item in results)
        or not bool(summary.get("decision", {}).get("production_ready"))
    )
    result = {"status": "failed" if failed else "complete", "report": "final_preproduction_report.html", "ended_utc": utc_now()}
    if failed:
        atomic_json(campaign_dir / "run_failed.json", result)
        print(f"[final-preproduction] STOP/FAIL · raport: {campaign_dir / 'final_preproduction_report.html'}", flush=True)
        return 1
    atomic_json(campaign_dir / "READY.json", result)
    print(f"[final-preproduction] PASS · raport: {campaign_dir / 'final_preproduction_report.html'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
