#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the factorised production-decision matrix and build one review file.

The suite deliberately covers every discrete production option while avoiding a
meaningless Cartesian product of unrelated numeric parameters. ``full`` is the
recommended decision run. ``exhaustive`` extends temporal width coverage to all
1..4 LSB widths. Every case uses the same source/dataset and writes its exact
environment plus the worker's runner_config.json.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from app.paths import PROJECT_ROOT
import signal
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

APP_VERSION = "2026.08.05.camera-entropy-production-assessment.8.0.2"
MAX_LSB_BITS = 4
ROOT = PROJECT_ROOT


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def env_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 0:
        raise SystemExit(f"{name} must be >= 0")
    return value


def minimum_input_bits(bits: int, credit: float) -> int:
    if not 1 <= bits <= MAX_LSB_BITS or not math.isfinite(credit) or not 0 < credit <= bits:
        raise ValueError("invalid LSB/credit combination")
    return max(512, int(math.ceil(512.0 * bits / credit / 8.0) * 8))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_slug(value: str) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "-" for character in value.lower())
    return "-".join(part for part in result.split("-") if part)


def credit_for(mode: str, bits: int) -> float:
    key = f"ASSESSMENT_CREDIT_{mode.upper()}_{bits}"
    defaults = {
        ("xor", 1): 0.50, ("xor", 2): 0.50,
        ("delta", 1): 0.50, ("delta", 2): 0.50,
    }
    return float(os.environ.get(key, str(defaults.get((mode, bits), 0.25))))


def base_case(stage: str, case_id: str, label: str, **overrides: Any) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "SAMPLE_MODE": "xor", "LSB_BITS": 1, "ENTROPY_CREDIT_BITS_PER_PIXEL": 0.5,
        "PAIRING_MODE": "disjoint", "PAIR_LAG_FRAMES": 4,
        "SPATIAL_MASK_PATTERN": "full", "SPATIAL_SAMPLING": "full",
        "SPATIAL_STEP_X": 1, "SPATIAL_STEP_Y": 1, "SPATIAL_PHASE_X": 0, "SPATIAL_PHASE_Y": 0,
        "SPATIAL_BLOCK_WIDTH": 4, "SPATIAL_BLOCK_HEIGHT": 4,
        "TEMPORAL_SPATIAL_OFFSET_X": 0, "TEMPORAL_SPATIAL_OFFSET_Y": 0,
        "SERIALIZATION_ORDER": "row-major", "SERIALIZATION_TILE_WIDTH": 16, "SERIALIZATION_TILE_HEIGHT": 16,
        "SPATIAL_COMPARISON": 0, "DUAL_WEAVE_COMPARISON": 0,
        "DUAL_WEAVE_ORDERS": "row-major", "DUAL_WEAVE_ALIGNMENTS": "same-group",
    }
    parameters.update(overrides)
    bits = int(parameters["LSB_BITS"]); credit = float(parameters["ENTROPY_CREDIT_BITS_PER_PIXEL"])
    parameters.setdefault("CONDITIONER_INPUT_BITS", max(env_int("ASSESSMENT_DEFAULT_CONDITIONER_INPUT_BITS", 2048), minimum_input_bits(bits, credit)))
    return {"id": safe_slug(case_id), "stage": stage, "label": label, "parameters": parameters}


def build_cases(level: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    # 1) Complete supported source-symbol matrix: 3 modes × 1..4 LSB.
    for mode in ("xor", "direct", "delta"):
        for bits in range(1, MAX_LSB_BITS + 1):
            cases.append(base_case(
                "lsb-mode-width", f"{mode}-lsb{bits}", f"{mode} / {bits} LSB",
                SAMPLE_MODE=mode, LSB_BITS=bits,
                ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for(mode, bits),
            ))

    # 2) Pairing/lag matrix. Full tests the likely production widths 1 and 2;
    # exhaustive extends the same matrix to 3 and 4.
    widths = (1,) if level == "quick" else (1, 2) if level == "full" else (1, 2, 3, 4)
    pairing_modes = ("disjoint",) if level == "quick" else ("disjoint", "sliding")
    temporal_modes = ("xor",) if level == "quick" else ("xor", "delta")
    for mode in temporal_modes:
        for bits in widths:
            for pairing in pairing_modes:
                for lag in (1, 2, 4, 8):
                    if pairing == "disjoint" and lag == 4:
                        # Already covered by the primary LSB matrix.
                        continue
                    cases.append(base_case(
                        "temporal-pairing", f"{mode}-lsb{bits}-{pairing}-k{lag}",
                        f"{mode} / {bits} LSB / {pairing} / k={lag}",
                        SAMPLE_MODE=mode, LSB_BITS=bits, PAIRING_MODE=pairing, PAIR_LAG_FRAMES=lag,
                        ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for(mode, bits),
                    ))

    # 3) Every public production-relevant spatial/serialization profile.
    spatial = [
        ("full-row-major", "Full / row-major", {}),
        ("checker-even", "Checkerboard even", {"SPATIAL_MASK_PATTERN": "checkerboard-even", "SPATIAL_SAMPLING": "checkerboard-even"}),
        ("checker-odd", "Checkerboard odd", {"SPATIAL_MASK_PATTERN": "checkerboard-odd", "SPATIAL_SAMPLING": "checkerboard-odd"}),
        ("grid2", "Grid 2×2 phase 0,0", {"SPATIAL_MASK_PATTERN": "grid", "SPATIAL_STEP_X": 2, "SPATIAL_STEP_Y": 2}),
        ("grid4", "Grid 4×4 phase 0,0", {"SPATIAL_MASK_PATTERN": "grid", "SPATIAL_STEP_X": 4, "SPATIAL_STEP_Y": 4}),
        ("block4", "Block 4×4 phase 1,2", {"SPATIAL_MASK_PATTERN": "block", "SPATIAL_PHASE_X": 1, "SPATIAL_PHASE_Y": 2}),
        ("offset11", "Temporal-spatial offset +1,+1", {"TEMPORAL_SPATIAL_OFFSET_X": 1, "TEMPORAL_SPATIAL_OFFSET_Y": 1}),
        ("offset22", "Temporal-spatial offset +2,+2", {"TEMPORAL_SPATIAL_OFFSET_X": 2, "TEMPORAL_SPATIAL_OFFSET_Y": 2}),
        ("serpentine", "Full / serpentine", {"SERIALIZATION_ORDER": "serpentine"}),
        ("tile16", "Full / tile-interleave 16×16", {"SERIALIZATION_ORDER": "tile-interleave"}),
    ]
    for ident, label, overrides in spatial:
        cases.append(base_case(
            "spatial-serialization", ident, label,
            SAMPLE_MODE="xor", LSB_BITS=2, ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for("xor", 2), **overrides,
        ))

    # 4) Entropy-credit sensitivity.  This does not prove a credit; it shows
    # how an externally selected conservative budget changes the minimum SHA3
    # input block and attainable output rate on the same source symbols.
    credit_sets = {
        1: (0.125, 0.25, 0.50, 0.75),
        2: (0.25, 0.50, 0.75, 1.00),
    }
    for bits, credits in credit_sets.items():
        for credit in credits:
            cases.append(base_case(
                "entropy-credit", f"xor-lsb{bits}-credit-{str(credit).replace('.', 'p')}",
                f"xor / {bits} LSB / credit {credit:g} bit/pixel",
                SAMPLE_MODE="xor", LSB_BITS=bits,
                ENTROPY_CREDIT_BITS_PER_PIXEL=credit,
                CONDITIONER_INPUT_BITS=minimum_input_bits(bits, credit),
            ))

    # 5) Conditioner compression sweep for the leading two-LSB temporal source.
    conditioner_sizes = (2048, 8192, 65536) if level == "quick" else (2048, 4096, 8192, 16384, 65536)
    if level == "exhaustive": conditioner_sizes = (2048, 4096, 8192, 16384, 32768, 65536)
    for size in conditioner_sizes:
        cases.append(base_case(
            "conditioner", f"sha3-input-{size}", f"SHA3-512 input {size} bit",
            SAMPLE_MODE="xor", LSB_BITS=2, ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for("xor", 2), CONDITIONER_INPUT_BITS=size,
        ))

    # 6) One run per lag produces both orders × all three alignments, i.e. 18
    # dual-weave variants across k=2,4,8.
    for lag in (2, 4, 8):
        cases.append(base_case(
            "dual-weave", f"dual-weave-k{lag}", f"Dual weave all variants / k={lag}",
            SAMPLE_MODE="xor", LSB_BITS=1, ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for("xor", 1),
            PAIRING_MODE="disjoint", PAIR_LAG_FRAMES=lag,
            SPATIAL_MASK_PATTERN="legacy", SPATIAL_SAMPLING="checkerboard-even",
            DUAL_WEAVE_COMPARISON=1, DUAL_WEAVE_ORDERS="row-major,serpentine",
            DUAL_WEAVE_ALIGNMENTS="same-group,stagger-1,stagger-2", CONDITIONER_INPUT_BITS=2048,
        ))

    # 7) Repeat the main candidates to expose run-to-run variability.
    repeats = 1 if level == "quick" else 3
    for candidate, bits in (("xor-lsb1", 1), ("xor-lsb2", 2), ("direct-lsb1", 1)):
        mode = candidate.split("-")[0]
        for repetition in range(1, repeats + 1):
            cases.append(base_case(
                "reproducibility", f"{candidate}-repeat-{repetition}", f"{candidate} / repeat {repetition}",
                SAMPLE_MODE=mode, LSB_BITS=bits, ENTROPY_CREDIT_BITS_PER_PIXEL=credit_for(mode, bits),
            ))
    return cases


def redacted_source_environment(environment: dict[str, str]) -> dict[str, str]:
    allowed = (
        "SOURCE_TYPE", "DEVICE", "WIDTH", "HEIGHT", "CAMERA_FPS", "EXPOSURE",
        "TLS_HOST", "TLS_PORT", "TLS_SERVER_NAME", "RTSP_URL_FILE", "DATASET_DIR",
        "DATASET_START_FRAME", "DATASET_MAX_FRAMES", "DATASET_REALTIME", "DATASET_RATE",
        "DATASET_FOLLOW", "DATASET_VERIFY_HASHES",
    )
    return {key: environment[key] for key in allowed if key in environment}


def main() -> int:
    level = os.environ.get("ASSESSMENT_LEVEL", "full").strip().lower()
    if level not in {"quick", "full", "exhaustive"}:
        raise SystemExit("ASSESSMENT_LEVEL must be quick, full or exhaustive")
    data_root = Path(os.environ.get("DATA_ROOT", str(ROOT / "data"))).expanduser().resolve()
    campaign = os.environ.get("CAMPAIGN", f"production-assessment-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    campaign_dir = data_root / campaign
    if campaign_dir.exists():
        raise SystemExit(f"campaign directory already exists: {campaign_dir}")
    (campaign_dir / "runs").mkdir(parents=True)
    (campaign_dir / "cases").mkdir()
    (campaign_dir / "logs").mkdir()

    base_environment = dict(os.environ)
    common = {
        "DATA_ROOT": str(campaign_dir),
        "WARMUP_SECONDS": str(env_int("ASSESSMENT_WARMUP_SECONDS", int(os.environ.get("WARMUP_SECONDS", "0")))),
        "CALIBRATION_PAIRS": str(env_int("ASSESSMENT_CALIBRATION_PAIRS", int(os.environ.get("CALIBRATION_PAIRS", "128")))),
        "DIAGNOSTIC_VN_BYTES": "0", "ENABLE_VON_NEUMANN": "0",
        "CONDITIONED_BYTES": str(env_int("ASSESSMENT_CONDITIONED_BYTES", int(os.environ.get("CONDITIONED_BYTES", "524288")))),
        "VALIDATION_BYTES": str(env_int("ASSESSMENT_VALIDATION_BYTES", int(os.environ.get("VALIDATION_BYTES", "2097152")))),
        "WEB_IMAGES": "0", "MASK_SNAPSHOT_IMAGES": "0", "LIVE_BYTE_DIAGNOSTICS": "0",
        "LIVE_HEATMAP_INTERVAL_SECONDS": "0", "BINARY_GEOMETRY_REPORT": "0",
        "DATASET_FOLLOW": os.environ.get("ASSESSMENT_DATASET_FOLLOW", "0"),
        "CORRELATION_EVERY": os.environ.get("ASSESSMENT_CORRELATION_EVERY", "20"),
    }
    cases = build_cases(level)
    config = {
        "schema": "camera-entropy-production-assessment-config-v1",
        "app_version": APP_VERSION, "created_utc": utc_now(), "campaign": campaign,
        "level": level, "max_lsb_bits": MAX_LSB_BITS, "case_count": len(cases),
        "source": redacted_source_environment(base_environment), "common_environment": common,
        "coverage": {
            "lsb_mode_width": "xor/direct/delta × 1..4",
            "temporal_pairing": "factorised pairing mode × k=1,2,4,8; widths depend on assessment level",
            "spatial_serialization": "all public production spatial profiles",
            "entropy_credit": "sensitivity sweep for conservative external credit on xor LSB1/LSB2",
            "conditioner": "SHA3-512 input size sweep",
            "dual_weave": "2 orders × 3 alignments × k=2,4,8",
            "reproducibility": "main candidates repeated",
            "not_cartesian": "Numeric ranges are factorised; the suite does not claim to enumerate infinite/continuous combinations.",
        },
        "cases": cases,
    }
    atomic_json(campaign_dir / "assessment_config.json", config)

    stop_requested = False
    child: subprocess.Popen[str] | None = None
    def handle_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        if child is not None and child.poll() is None:
            child.terminate()
    signal.signal(signal.SIGINT, handle_stop); signal.signal(signal.SIGTERM, handle_stop)

    failures = 0
    for index, case in enumerate(cases, 1):
        if stop_requested:
            break
        case_id = case["id"]
        run_dir = campaign_dir / "runs" / case_id
        log_path = campaign_dir / "logs" / f"{case_id}.log"
        state_path = campaign_dir / "cases" / f"{index:03d}-{case_id}.json"
        state = {**case, "index": index, "total": len(cases), "status": "running", "started_utc": utc_now(), "run_dir": f"runs/{case_id}", "log": f"logs/{case_id}.log"}
        atomic_json(state_path, state)
        environment = base_environment | common | {key: str(value) for key, value in case["parameters"].items()} | {
            "RUN_NAME": f"runs/{case_id}", "RUN_DIR": str(run_dir),
        }
        print(f"[assessment {index}/{len(cases)}] {case['stage']} :: {case['label']}", flush=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
            child = subprocess.Popen([str(ROOT / "scripts" / "run" / "run_one.sh")], cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert child.stdout is not None
            for line in child.stdout:
                sys.stdout.write(line); log_stream.write(line); log_stream.flush()
            returncode = child.wait()
        child = None
        state.update({"status": "complete" if returncode == 0 else "failed", "exit_code": returncode, "ended_utc": utc_now()})
        atomic_json(state_path, state)
        failures += int(returncode != 0)
        subprocess.run([sys.executable, "-m", "app.reporting.summarize_production_assessment", str(campaign_dir)], cwd=ROOT, check=False, stdout=subprocess.DEVNULL)
        if returncode != 0 and os.environ.get("ASSESSMENT_CONTINUE_ON_ERROR", "1") != "1":
            break

    subprocess.run([sys.executable, "-m", "app.reporting.summarize_production_assessment", str(campaign_dir)], cwd=ROOT, check=True)
    result = {"status": "stopped" if stop_requested else "failed" if failures else "complete", "failed_cases": failures, "report": "production_assessment_report.html", "ended_utc": utc_now()}
    if failures or stop_requested:
        atomic_json(campaign_dir / "run_failed.json", result)
        return 130 if stop_requested else 1
    atomic_json(campaign_dir / "READY.json", result)
    print(f"Production assessment report: {campaign_dir / 'production_assessment_report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
