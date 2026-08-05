#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent web control plane for Camera Entropy Distributed.

The controller stays online on the public/default port while compute workers are
spawned on a loopback-only port.  It deliberately exposes only predefined
sources and a small allow-list of test parameters; credentials and RTSP URLs
remain in server-side configuration files.
"""
from __future__ import annotations

import argparse
import csv
import hmac
import json
import logging
import mimetypes
import os
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from app.reporting.spatial_docs import register_documentation_routes
from app.paths import PROJECT_ROOT, TEMPLATES_ROOT, STATIC_ROOT
from app.control.profile_catalog import ALLOWED_PROFILES, profile_rows
# CAMERA_ENTROPY_SPATIAL_V7_7
APP_VERSION = "2026.08.05.camera-entropy-distributed-control.8.0.1"
DATASET_FORMAT = "camera-entropy-frame-buffer-v1"
READABLE_DATASET_STATUSES = {"recording", "complete", "stopped", "failed"}
SUPPORTED_DATASET_STORAGE_MODES = {"y8", "lsb-packed"}
REQUIRED_DATASET_INDEX_FIELDS = {
    "sequence",
    "source_frame_id",
    "chunk",
    "offset",
    "payload_bytes",
    "captured_unix_ns",
    "captured_monotonic_ns",
    "source_warmup_seconds",
    "source_connection_generation",
}

PARAMETER_HELP = {
    "source_id": "Zdefiniowane po stronie serwera źródło V4L2, TLS-Y, RTSP albo zapisany dataset Y/LSB. Dane uwierzytelniające i ścieżki nie trafiają do przeglądarki.",
    "profile": "Gotowy zestaw parametrów i rozmiarów testu. Profile spatial wymuszają opisaną geometrię.",
    "assessment_level": "Poziom profilu PRODUCTION: quick wykonuje screening, full jest rekomendowany, exhaustive rozszerza macierz pairingu i lagów na wszystkie 1..4 LSB.",
    "exposure": "Ręczna ekspozycja źródła. Zmiana wpływa na fizykę źródła i wymaga nowej kalibracji.",
    "pair_lag_frames": "Odstęp czasowy k pomiędzy ramkami. To nie jest odległość pomiędzy pikselami.",
    "sample_mode": "xor = czasowy XOR, direct = dolne bity bieżącej klatki Y, delta = reszta Y_t-Y_(t-k) modulo 256.",
    "lsb_bits": "Liczba pobieranych dolnych bitów próbki: 1..4. Więcej danych nie oznacza automatycznie takiej samej liczby bitów entropii.",
    "entropy_credit_bits_per_pixel": "Konserwatywny budżet entropii na wybrany piksel. Jest niezależny od lsb_bits i musi wynikać z oceny źródła.",
    "spatial_sampling": "Starsza opcja zgodności. Jest używana tylko przy spatial_mask_pattern=legacy.",
    "spatial_mask_pattern": "Właściwa maska produkcyjna: legacy, full, checkerboard, grid albo block.",
    "spatial_step_x": "Poziomy krok siatki. Dla 4 wybierana jest jedna klasa słupków modulo 4.",
    "spatial_step_y": "Pionowy krok siatki.",
    "spatial_phase_x": "Wybrana klasa modulo X albo lokalna pozycja X w bloku.",
    "spatial_phase_y": "Wybrana klasa modulo Y albo lokalna pozycja Y w bloku.",
    "spatial_block_width": "Szerokość bloku dla wzorca block.",
    "spatial_block_height": "Wysokość bloku dla wzorca block.",
    "temporal_spatial_offset_x": "Porównuje Y_t(x,y) z Y_(t-k)(x+dx,y+dy). Krawędzie są odrzucane, nigdy zawijane.",
    "temporal_spatial_offset_y": "Pionowa składowa przesunięcia starszego piksela.",
    "serialization_order": "Kolejność bitów przed VN/SHA3. Reordering nie tworzy entropii i nie zastępuje conditionera.",
    "serialization_tile_width": "Szerokość kafla dla tile-interleave.",
    "serialization_tile_height": "Wysokość kafla dla tile-interleave.",
    "conditioner_input_bits": "Liczba surowych bitów kompresowanych do jednego wyniku SHA3-512.",
    "warmup_seconds": "Czas stabilizacji źródła przed kalibracją. Wyjście jest w tym czasie zablokowane.",
    "calibration_pairs": "Liczba par ramek użyta do wyznaczenia i zamrożenia aktywnej maski pikseli.",
    "conditioned_mib": "Docelowy rozmiar finalnego strumienia SHA3-512.",
    "diagnostic_vn_mib": "Rozmiar równoległego wyniku Von Neumanna. Jest diagnostyczny.",
    "validation_mib": "Limit plików walidacyjnych przed conditionerem.",
    "stream_stats_window_pairs": "Liczba par w jednym oknie statystyk streamingowych. Te statystyki obejmują cały przetworzony dataset bez zapisywania pełnego strumienia raw.",
    "dataset_verify_workers": "Liczba równoległych odczytów SHA-256. Dla NVMe/SSD zwykle 4–16, dla pojedynczego dysku talerzowego 1–2.",
    "dataset_verify_progress_seconds": "Interwał komunikatów postępu weryfikacji integralności datasetu.",
    "final_preprod_workers": "Liczba równoległych wariantów ostatecznej kwalifikacji. Maksymalnie pięć.",
    "final_preprod_status_interval_seconds": "Interwał heartbeat kampanii podczas długiego przetwarzania całego datasetu.",
    "dataset_verify_cache": "Ponownie używa pełnej weryfikacji SHA-256, gdy checksum manifest, rozmiary i czasy modyfikacji chunków nie zmieniły się.",
    "runs": "Liczba przebiegów używana przez profile kwalifikacyjne.",
    "first_warmup_seconds": "Warm-up pierwszego przebiegu kampanii.",
    "next_warmup_seconds": "Warm-up kolejnych przebiegów kampanii.",
    "live_heatmap_interval_seconds": "Interwał zapisu zbiorczej heatmapy PNG; 0 wyłącza zapis.",
    "live_heatmap_max_stages": "Maksymalna liczba etapów pipeline pokazywanych jednocześnie.",
    "live_heatmap_min_bytes": "Minimalna liczba bajtów etapu przed renderowaniem heatmapy.",
    "source_frame_timeout_seconds": "Maksymalny czas oczekiwania na następną ramkę TLS-Y.",
    "source_reconnect_attempts": "Liczba prób ponownego połączenia przed produkcją.",
    "source_reconnect_backoff_seconds": "Przerwa pomiędzy próbami ponownego połączenia.",
    "web_images": "Generuje obrazy podglądu workera; zwiększa narzut CPU i I/O.",
    "mask_snapshot_images": "Archiwizuje okresowe obrazy masek i ich różnic.",
    "live_byte_diagnostics": "Włącza histogramy bajtów i bieżące heatmapy etapów pipeline.",
}

ALLOWED_SPATIAL = {
    "full",
    "checkerboard",
    "checkerboard-even",
    "checkerboard-odd",
    "grid2x2",
}
ALLOWED_SOURCE_TYPES = {"v4l2", "tls-y", "rtsp", "dataset-y"}
MIB = 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a JSON object without allowing a missing or malformed file to break the UI."""
    value = read_json(path, {})
    return value if isinstance(value, dict) else {}


def is_process_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def redact_source(source: dict[str, Any]) -> dict[str, Any]:
    safe = {k: v for k, v in source.items() if k not in {"env", "rtsp_url", "password", "secret"}}
    safe["id"] = str(source.get("id", ""))
    safe["label"] = str(source.get("label", safe["id"]))
    safe["source_type"] = str(source.get("source_type", ""))
    return safe


@dataclass(frozen=True)
class Settings:
    root: Path
    data_root: Path
    sources_file: Path
    credentials_file: Path
    secret_file: Path
    host: str
    port: int
    worker_host: str
    worker_port: int
    auth_mode: str
    secure_cookie: bool
    trust_proxy: bool
    share_token_file: Path | None


def load_sources(path: Path) -> list[dict[str, Any]]:
    document = read_json(path)
    if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
        raise RuntimeError(f"Nieprawidłowy lub brakujący plik źródeł: {path}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in document["sources"]:
        if not isinstance(raw, dict):
            raise RuntimeError("Każde źródło w sources.json musi być obiektem")
        source_id = str(raw.get("id", "")).strip()
        source_type = str(raw.get("source_type", "")).strip()
        env = raw.get("env", {})
        if not source_id or source_id in seen:
            raise RuntimeError(f"Nieprawidłowe lub zduplikowane id źródła: {source_id!r}")
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise RuntimeError(f"Źródło {source_id}: nieobsługiwany source_type={source_type!r}")
        if not isinstance(env, dict) or not all(isinstance(k, str) for k in env):
            raise RuntimeError(f"Źródło {source_id}: env musi być obiektem")
        item = dict(raw)
        item["id"] = source_id
        item["source_type"] = source_type
        item["label"] = str(raw.get("label", source_id))
        item["env"] = {str(k): str(v) for k, v in env.items()}
        result.append(item)
        seen.add(source_id)
    if not result:
        raise RuntimeError("Plik źródeł nie zawiera żadnego źródła")
    return result


class JobManager:
    """Persist and supervise compute jobs.

    Live camera sources remain exclusive. Read-only dataset-y jobs may run in
    parallel on independently allocated loopback ports.
    """

    ACTIVE_STATUSES = {"starting", "running", "stopping"}

    def __init__(self, settings: Settings, sources: list[dict[str, Any]], logger: logging.Logger) -> None:
        self.settings = settings
        self.sources = {item["id"]: item for item in sources}
        self.logger = logger
        self.state_root = settings.data_root / ".control"
        self.jobs_root = self.state_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.max_dataset_jobs = max(1, int(os.environ.get("MAX_DATASET_JOBS", min(16, os.cpu_count() or 4))))
        self._recover_active_jobs()

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_root / f"{job_id}.json"

    def _log_path(self, job_id: str) -> Path:
        return self.jobs_root / f"{job_id}.log"

    def _load_job(self, job_id: str) -> dict[str, Any] | None:
        value = read_json(self._job_path(job_id))
        return value if isinstance(value, dict) else None

    def _save_job(self, job: dict[str, Any]) -> None:
        atomic_json(self._job_path(str(job["id"])), job)

    def _recover_active_jobs(self) -> None:
        for path in self.jobs_root.glob("*.json"):
            item = read_json(path)
            if not isinstance(item, dict) or item.get("status") not in self.ACTIVE_STATUSES:
                continue
            pid = int(item.get("pid") or 0)
            if is_process_alive(pid):
                self.logger.warning("Odzyskano aktywny job %s z pid=%s", item.get("id"), pid)
                continue
            item["status"] = "interrupted"
            item["ended_at"] = utc_now()
            item["message"] = "Proces nie działał podczas startu control servera"
            self._save_job(item)

    def _finalize_dead_job(self, job: dict[str, Any]) -> dict[str, Any]:
        output_root = Path(str(job.get("output_root", "")))
        ready = any(
            output_root.joinpath(name).exists()
            for name in (
                "READY.json", "qualification_report.html", "lsb_campaign_report.html",
                "production_assessment_report.html", "final_preproduction_report.html",
                "global_campaign_report.html",
            )
        )
        failed = output_root.joinpath("run_failed.json").exists()
        if output_root.is_dir() and not failed:
            failed = any(output_root.glob("**/run_failed.json"))
        job["status"] = "complete" if ready else "failed" if failed else "interrupted"
        job.setdefault("ended_at", utc_now())
        job.setdefault("message", "Stan odtworzony po restarcie control servera")
        self._save_job(job)
        return job

    def _reconcile_all(self) -> None:
        for path in self.jobs_root.glob("*.json"):
            job = read_json(path)
            if not isinstance(job, dict) or job.get("status") not in self.ACTIVE_STATUSES:
                continue
            job_id = str(job.get("id", ""))
            process = self.processes.get(job_id)
            if process is not None and process.poll() is None:
                continue
            pid = int(job.get("pid") or 0)
            if not is_process_alive(pid):
                self._finalize_dead_job(job)
                self.processes.pop(job_id, None)

    def active_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            self._reconcile_all()
            rows = [
                item for item in self.list_jobs(limit=500)
                if item.get("status") in self.ACTIVE_STATUSES
            ]
            rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return rows

    def current(self, job_id: str | None = None) -> dict[str, Any] | None:
        with self.lock:
            active = self.active_jobs()
            if job_id is not None:
                return next((item for item in active if str(item.get("id")) == job_id), None)
            return active[0] if active else None

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.jobs_root.glob("*.json"):
            item = read_json(path)
            if isinstance(item, dict):
                rows.append(item)
        rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return rows[:limit]

    def _allocate_worker_port(self, span: int = 1) -> int:
        if span < 1 or span > 64:
            raise ValueError("Nieprawidłowy rozmiar rezerwacji portów")
        active_ports: set[int] = set()
        for job in self.active_jobs():
            base = int(job.get("worker_port") or 0)
            reserved = max(1, int(job.get("worker_port_span") or 1))
            if base > 0:
                active_ports.update(range(base, base + reserved))
        for port in range(self.settings.worker_port, self.settings.worker_port + 256 - span):
            if all(candidate not in active_ports for candidate in range(port, port + span)):
                return port
        raise RuntimeError("Brak wolnego zakresu portów workera w puli control servera")

    @staticmethod
    def _positive_int(payload: dict[str, Any], name: str, minimum: int, maximum: int) -> int | None:
        value = payload.get(name)
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} musi być liczbą całkowitą") from exc
        if not minimum <= number <= maximum:
            raise ValueError(f"{name} musi być w zakresie {minimum}..{maximum}")
        return number

    def _prepare_dataset_environment(self, env: dict[str, str]) -> dict[str, Any]:
        raw_dir = str(env.get("DATASET_DIR", "data/frame-buffer-latest")).strip()
        if not raw_dir:
            raw_dir = "data/frame-buffer-latest"
        dataset_dir = Path(raw_dir).expanduser()
        if not dataset_dir.is_absolute():
            dataset_dir = self.settings.root / dataset_dir
        dataset_dir = dataset_dir.resolve()
        if not dataset_dir.is_dir():
            raise ValueError(f"Dataset nie istnieje lub nie jest katalogiem: {dataset_dir}")

        manifest_path = dataset_dir / "manifest.json"
        index_path = dataset_dir / "frames.csv"
        chunks_path = dataset_dir / "chunks"
        if not manifest_path.is_file():
            raise ValueError(f"Brak manifestu datasetu: {manifest_path}")
        if not index_path.is_file():
            raise ValueError(f"Brak indeksu klatek datasetu: {index_path}")
        if not chunks_path.is_dir():
            raise ValueError(f"Brak katalogu chunków datasetu: {chunks_path}")

        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError(f"Manifest datasetu nie jest poprawnym obiektem JSON: {manifest_path}")
        if manifest.get("format") != DATASET_FORMAT:
            raise ValueError(
                f"Nieobsługiwany format datasetu: {manifest.get('format')!r}; "
                f"oczekiwano {DATASET_FORMAT!r}"
            )
        status = str(manifest.get("status", ""))
        if status not in READABLE_DATASET_STATUSES:
            raise ValueError(f"Dataset ma nieobsługiwany status: {status!r}")
        storage_mode = str(manifest.get("storage_mode", ""))
        if storage_mode not in SUPPORTED_DATASET_STORAGE_MODES:
            raise ValueError(f"Dataset ma nieobsługiwany tryb zapisu: {storage_mode!r}")

        try:
            width = int(manifest.get("width", 0))
            height = int(manifest.get("height", 0))
            frame_payload_bytes = int(manifest.get("frame_payload_bytes", 0))
            frame_count = int(manifest.get("frame_count", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Manifest datasetu zawiera nieprawidłowe wartości liczbowe: {manifest_path}") from exc
        if width <= 0 or height <= 0:
            raise ValueError(f"Dataset ma nieprawidłowy rozmiar klatki: {width}x{height}")
        expected_payload = width * height if storage_mode == "y8" else (width * height + 7) // 8
        if frame_payload_bytes != expected_payload:
            raise ValueError(
                f"Niezgodny rozmiar payloadu datasetu: manifest={frame_payload_bytes}, "
                f"oczekiwano={expected_payload}"
            )
        if status != "recording" and frame_count < 2:
            raise ValueError(
                f"Zakończony dataset zawiera zbyt mało klatek: {frame_count}; wymagane co najmniej 2"
            )

        try:
            with index_path.open("r", encoding="utf-8", newline="") as handle:
                header_line = handle.readline()
        except OSError as exc:
            raise ValueError(f"Nie można odczytać indeksu datasetu: {index_path}: {exc}") from exc
        if not header_line.endswith("\n"):
            raise ValueError(f"Nagłówek indeksu datasetu jest niekompletny: {index_path}")
        try:
            fields = next(csv.reader([header_line]))
        except (csv.Error, StopIteration) as exc:
            raise ValueError(f"Nieprawidłowy nagłówek indeksu datasetu: {index_path}") from exc
        missing = REQUIRED_DATASET_INDEX_FIELDS.difference(fields)
        if missing:
            raise ValueError(
                "Indeks datasetu nie zawiera wymaganych kolumn: " + ", ".join(sorted(missing))
            )

        env["DATASET_DIR"] = str(dataset_dir)
        env["WIDTH"] = str(width)
        env["HEIGHT"] = str(height)
        env["DATASET_AVAILABLE_FRAMES"] = str(frame_count)
        env["DATASET_STATUS"] = status
        return manifest

    def _build_environment(
        self,
        payload: dict[str, Any],
        source: dict[str, Any],
        profile: str,
        job_id: str,
        worker_port: int,
    ) -> tuple[dict[str, str], str, Path]:
        env = os.environ.copy()
        env.update(source["env"])
        env["SOURCE_TYPE"] = source["source_type"]
        env["DATA_ROOT"] = str(self.settings.data_root)
        env["HOST"] = self.settings.worker_host
        env["API_HOST"] = self.settings.worker_host
        env["DISPLAY_HOST"] = "127.0.0.1"
        env["PORT"] = str(worker_port)
        env["WORKER_PORT_BASE"] = str(worker_port)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "UTF-8"

        dataset_manifest: dict[str, Any] | None = None
        if source["source_type"] == "dataset-y":
            dataset_manifest = self._prepare_dataset_environment(env)
            scope = str(payload.get("dataset_scope", "output-limit")).strip().lower() or "output-limit"
            if profile == "final-preproduction":
                scope = "all-available"
            if scope not in {"output-limit", "all-available", "range", "follow"}:
                raise ValueError("dataset_scope musi być output-limit, all-available, range albo follow")
            start_frame = self._positive_int(payload, "dataset_start_frame", 0, 10**12) or 0
            max_frames = self._positive_int(payload, "dataset_max_frames", 0, 10**12) or 0
            available = int(dataset_manifest.get("frame_count", 0) or 0)
            status = str(dataset_manifest.get("status", ""))
            if scope == "all-available":
                if available < 2:
                    raise ValueError("Dataset nie ma jeszcze wystarczającej liczby opublikowanych klatek")
                start_frame = 0
                max_frames = available
                env["DATASET_FOLLOW"] = "0"
                env["EXIT_ON_OUTPUT_LIMIT"] = "0"
            elif scope == "range":
                if max_frames <= 0:
                    raise ValueError("Zakres datasetu wymaga DATASET_MAX_FRAMES > 0")
                if available and start_frame >= available:
                    raise ValueError(f"Początek zakresu {start_frame} przekracza dostępne klatki ({available})")
                max_frames = min(max_frames, max(0, available - start_frame)) if available else max_frames
                env["DATASET_FOLLOW"] = "0"
                env["EXIT_ON_OUTPUT_LIMIT"] = "0"
            elif scope == "follow":
                env["DATASET_FOLLOW"] = "1"
                max_frames = 0
                env["EXIT_ON_OUTPUT_LIMIT"] = "1"
            else:
                env["DATASET_FOLLOW"] = "1" if status == "recording" and bool(payload.get("dataset_follow", False)) else "0"
                env["EXIT_ON_OUTPUT_LIMIT"] = "1"
            env["DATASET_SCOPE"] = scope
            env["DATASET_START_FRAME"] = str(start_frame)
            env["DATASET_MAX_FRAMES"] = str(max_frames)
            env["DATASET_VERIFY_HASHES"] = "1" if profile == "final-preproduction" or payload.get("dataset_verify_hashes", False) else "0"
            verify_cache = payload.get(
                "dataset_verify_cache", payload.get("final_preprod_verify_cache", True)
            )
            env["DATASET_VERIFY_CACHE"] = "1" if verify_cache else "0"
            dataset_verify_workers = self._positive_int(payload, "dataset_verify_workers", 1, 64)
            dataset_verify_progress = self._positive_int(payload, "dataset_verify_progress_seconds", 1, 60)
            env["DATASET_VERIFY_WORKERS"] = str(dataset_verify_workers or int(env.get("DATASET_VERIFY_WORKERS", "1")))
            env["DATASET_VERIFY_PROGRESS_SECONDS"] = str(dataset_verify_progress or int(env.get("DATASET_VERIFY_PROGRESS_SECONDS", "2")))
            if profile == "final-preproduction":
                final_workers = self._positive_int(payload, "final_preprod_workers", 1, 5)
                final_status_interval = self._positive_int(payload, "final_preprod_status_interval_seconds", 5, 600)
                env["FINAL_PREPROD_WORKERS"] = str(final_workers or min(5, max(1, (os.cpu_count() or 4) // 2)))
                env["FINAL_PREPROD_VERIFY_WORKERS"] = env["DATASET_VERIFY_WORKERS"]
                env["FINAL_PREPROD_PROGRESS_INTERVAL_SECONDS"] = env["DATASET_VERIFY_PROGRESS_SECONDS"]
                env["FINAL_PREPROD_STATUS_INTERVAL_SECONDS"] = str(final_status_interval or 30)
                env["FINAL_PREPROD_VERIFY_CACHE"] = env["DATASET_VERIFY_CACHE"]
            env["DATASET_REALTIME"] = "1" if payload.get("dataset_realtime", False) else "0"
        else:
            ignored_dataset_keys = sorted(
                key for key in payload
                if key.startswith("dataset_") or key.startswith("final_preprod_")
            )
            if ignored_dataset_keys:
                self.logger.debug(
                    "Ignoruję parametry dataset-y dla źródła LIVE %s: %s",
                    source.get("id", source.get("source_type", "unknown")),
                    ", ".join(ignored_dataset_keys),
                )

        env["WEB_IMAGES"] = "1" if payload.get("web_images", False) else "0"
        env["MASK_SNAPSHOT_IMAGES"] = "1" if payload.get("mask_snapshot_images", False) else "0"
        env["LIVE_BYTE_DIAGNOSTICS"] = "1" if payload.get("live_byte_diagnostics", False) else "0"

        exposure = self._positive_int(payload, "exposure", 1, 1_000_000)
        warmup = self._positive_int(payload, "warmup_seconds", 0, 86_400)
        calibration = self._positive_int(payload, "calibration_pairs", 32, 1_000_000)
        pair_lag = self._positive_int(payload, "pair_lag_frames", 1, 4096)
        lsb_bits = self._positive_int(payload, "lsb_bits", 1, 4)
        vn_passes = self._positive_int(payload, "von_neumann_passes", 0, 4)
        pairing_mode = str(payload.get("pairing_mode", "")).strip()
        if pairing_mode and pairing_mode not in {"disjoint", "sliding"}:
            raise ValueError("pairing_mode musi być disjoint albo sliding")
        conditioner = str(payload.get("conditioner", "sha3-512")).strip().lower() or "sha3-512"
        if conditioner not in {"sha3-512", "none"}:
            raise ValueError("conditioner musi być sha3-512 albo none")
        assessment_level = str(payload.get("assessment_level", "full")).strip().lower() or "full"
        if assessment_level not in {"quick", "full", "exhaustive"}:
            raise ValueError("assessment_level musi być quick, full albo exhaustive")
        env["ASSESSMENT_LEVEL"] = assessment_level
        sample_mode = str(payload.get("sample_mode", "")).strip()
        if sample_mode and sample_mode not in {"xor", "direct", "delta"}:
            raise ValueError("sample_mode musi być xor, direct albo delta")

        entropy_credit_raw = str(payload.get("entropy_credit_bits_per_pixel", "")).strip().replace(",", ".")
        entropy_credit: float | None = None
        if entropy_credit_raw:
            try:
                entropy_credit = float(entropy_credit_raw)
            except ValueError as exc:
                raise ValueError("entropy_credit_bits_per_pixel musi być liczbą") from exc
            effective_lsb_bits = lsb_bits if lsb_bits is not None else int(env.get("LSB_BITS", "1"))
            if not 0.0 < entropy_credit <= effective_lsb_bits:
                raise ValueError("entropy_credit_bits_per_pixel musi być w (0, lsb_bits]")

        conditioner_input = self._positive_int(payload, "conditioner_input_bits", 512, 1_048_576)
        runs = self._positive_int(payload, "runs", 1, 100)
        first_warmup = self._positive_int(payload, "first_warmup_seconds", 0, 86_400)
        next_warmup = self._positive_int(payload, "next_warmup_seconds", 0, 86_400)
        live_heatmap_interval = self._positive_int(payload, "live_heatmap_interval_seconds", 0, 86_400)
        live_heatmap_max_stages = self._positive_int(payload, "live_heatmap_max_stages", 1, 32)
        live_heatmap_min_bytes = self._positive_int(payload, "live_heatmap_min_bytes", 256, 1_073_741_824)
        source_frame_timeout = self._positive_int(payload, "source_frame_timeout_seconds", 5, 3600)
        source_reconnect_attempts = self._positive_int(payload, "source_reconnect_attempts", 0, 100)
        source_reconnect_backoff = self._positive_int(payload, "source_reconnect_backoff_seconds", 0, 300)
        stream_stats_window_pairs = self._positive_int(payload, "stream_stats_window_pairs", 1, 1_000_000)

        for value, key in (
            (exposure, "EXPOSURE"), (warmup, "WARMUP_SECONDS"),
            (calibration, "CALIBRATION_PAIRS"), (pair_lag, "PAIR_LAG_FRAMES"),
            (lsb_bits, "LSB_BITS"), (runs, "RUNS"),
            (first_warmup, "FIRST_WARMUP_SECONDS"), (next_warmup, "NEXT_WARMUP_SECONDS"),
            (live_heatmap_interval, "LIVE_HEATMAP_INTERVAL_SECONDS"),
            (live_heatmap_max_stages, "LIVE_HEATMAP_MAX_STAGES"),
            (live_heatmap_min_bytes, "LIVE_HEATMAP_MIN_BYTES"),
            (source_frame_timeout, "SOURCE_FRAME_TIMEOUT_SECONDS"),
            (source_reconnect_attempts, "SOURCE_RECONNECT_ATTEMPTS"),
            (source_reconnect_backoff, "SOURCE_RECONNECT_BACKOFF_SECONDS"),
            (stream_stats_window_pairs, "STREAM_STATS_WINDOW_PAIRS"),
        ):
            if value is not None:
                env[key] = str(value)
        if pairing_mode:
            env["PAIRING_MODE"] = pairing_mode
        if sample_mode:
            env["SAMPLE_MODE"] = sample_mode
        if entropy_credit is not None:
            env["ENTROPY_CREDIT_BITS_PER_PIXEL"] = format(entropy_credit, ".12g")
        if vn_passes is not None:
            env["VON_NEUMANN_PASSES"] = str(vn_passes)
            env["ENABLE_VON_NEUMANN"] = "1" if vn_passes > 0 else "0"
        env["CONDITIONER"] = conditioner
        if conditioner == "none":
            env["CONDITIONED_BYTES"] = "0"
        elif conditioner_input is not None:
            if conditioner_input % 8:
                raise ValueError("conditioner_input_bits musi być wielokrotnością 8")
            env["CONDITIONER_INPUT_BITS"] = str(conditioner_input)

        spatial_mask_pattern = str(payload.get("spatial_mask_pattern", "")).strip()
        if spatial_mask_pattern:
            if spatial_mask_pattern not in {"legacy", "full", "checkerboard-even", "checkerboard-odd", "grid", "block"}:
                raise ValueError("Nieprawidłowy spatial_mask_pattern")
            env["SPATIAL_MASK_PATTERN"] = spatial_mask_pattern
        serialization_order = str(payload.get("serialization_order", "")).strip()
        if serialization_order:
            if serialization_order not in {"row-major", "serpentine", "tile-interleave"}:
                raise ValueError("Nieprawidłowy serialization_order")
            env["SERIALIZATION_ORDER"] = serialization_order
        for payload_key, env_key, minimum, maximum in (
            ("spatial_step_x", "SPATIAL_STEP_X", 1, 4096),
            ("spatial_step_y", "SPATIAL_STEP_Y", 1, 4096),
            ("spatial_phase_x", "SPATIAL_PHASE_X", 0, 4095),
            ("spatial_phase_y", "SPATIAL_PHASE_Y", 0, 4095),
            ("spatial_block_width", "SPATIAL_BLOCK_WIDTH", 1, 4096),
            ("spatial_block_height", "SPATIAL_BLOCK_HEIGHT", 1, 4096),
            ("serialization_tile_width", "SERIALIZATION_TILE_WIDTH", 1, 4096),
            ("serialization_tile_height", "SERIALIZATION_TILE_HEIGHT", 1, 4096),
            ("clip_fail_consecutive", "CLIP_FAIL_CONSECUTIVE", 1, 1000),
            ("control_check_seconds", "CONTROL_CHECK_SECONDS", 0, 86400),
            ("control_fail_consecutive", "CONTROL_FAIL_CONSECUTIVE", 1, 1000),
            ("shadow_fail_consecutive", "SHADOW_FAIL_CONSECUTIVE", 1, 1000),
        ):
            raw = str(payload.get(payload_key, "")).strip()
            if raw:
                try:
                    number = int(raw)
                except ValueError as exc:
                    raise ValueError(f"{payload_key} musi być liczbą całkowitą") from exc
                if not minimum <= number <= maximum:
                    raise ValueError(f"{payload_key} musi być w zakresie {minimum}..{maximum}")
                env[env_key] = str(number)
        for payload_key, env_key in (
            ("temporal_spatial_offset_x", "TEMPORAL_SPATIAL_OFFSET_X"),
            ("temporal_spatial_offset_y", "TEMPORAL_SPATIAL_OFFSET_Y"),
        ):
            raw = str(payload.get(payload_key, "")).strip()
            if raw:
                try:
                    number = int(raw)
                except ValueError as exc:
                    raise ValueError(f"{payload_key} musi być liczbą całkowitą") from exc
                if not -4096 <= number <= 4096:
                    raise ValueError(f"{payload_key} musi być w zakresie -4096..4096")
                env[env_key] = str(number)

        for payload_key, env_key, minimum, maximum in (
            ("mask_p1_min", "MASK_P1_MIN", 0.000001, 0.999999),
            ("mask_p1_max", "MASK_P1_MAX", 0.000001, 0.999999),
            ("mask_transition_min", "MASK_TRANSITION_MIN", 0.000001, 0.999999),
            ("mask_transition_max", "MASK_TRANSITION_MAX", 0.000001, 0.999999),
            ("mask_clip_max", "MASK_CLIP_MAX", 0.0, 1.0),
            ("max_active_clip_rate", "MAX_ACTIVE_CLIP_RATE", 0.0, 1.0),
            ("shadow_min_active_retention", "SHADOW_MIN_ACTIVE_RETENTION", 0.000001, 1.0),
            ("shadow_min_jaccard", "SHADOW_MIN_JACCARD", 0.000001, 1.0),
        ):
            raw = str(payload.get(payload_key, "")).strip().replace(",", ".")
            if raw:
                try:
                    number = float(raw)
                except ValueError as exc:
                    raise ValueError(f"{payload_key} musi być liczbą") from exc
                if not minimum <= number <= maximum:
                    raise ValueError(f"{payload_key} musi być w zakresie {minimum}..{maximum}")
                env[env_key] = format(number, ".12g")
        if float(env.get("MASK_P1_MIN", "0.30")) >= float(env.get("MASK_P1_MAX", "0.70")):
            raise ValueError("MASK_P1_MIN musi być mniejsze od MASK_P1_MAX")
        if float(env.get("MASK_TRANSITION_MIN", "0.20")) >= float(env.get("MASK_TRANSITION_MAX", "0.80")):
            raise ValueError("MASK_TRANSITION_MIN musi być mniejsze od MASK_TRANSITION_MAX")

        spatial = str(payload.get("spatial_sampling", "")).strip()
        if spatial and spatial not in ALLOWED_SPATIAL:
            raise ValueError("Nieobsługiwane spatial_sampling")
        pattern = env.get("SPATIAL_MASK_PATTERN", "full")
        # spatial_sampling is a legacy compatibility switch.  Disabled form
        # controls are not submitted by browsers, therefore modern patterns
        # must not inherit the historical checkerboard default from run_one.sh.
        if pattern == "legacy":
            env["SPATIAL_SAMPLING"] = spatial or "full"
        else:
            env["SPATIAL_SAMPLING"] = "full"
        step_x = int(env.get("SPATIAL_STEP_X", "1")); step_y = int(env.get("SPATIAL_STEP_Y", "1"))
        phase_x = int(env.get("SPATIAL_PHASE_X", "0")); phase_y = int(env.get("SPATIAL_PHASE_Y", "0"))
        block_width = int(env.get("SPATIAL_BLOCK_WIDTH", "4")); block_height = int(env.get("SPATIAL_BLOCK_HEIGHT", "4"))
        if pattern == "grid" and (phase_x >= step_x or phase_y >= step_y):
            raise ValueError("Dla grid faza X/Y musi być mniejsza od kroku X/Y")
        if pattern == "block" and (phase_x >= block_width or phase_y >= block_height):
            raise ValueError("Dla block faza X/Y musi mieścić się w bloku")

        for field, env_name in (
            ("diagnostic_vn_mib", "DIAGNOSTIC_VN_BYTES"),
            ("conditioned_mib", "CONDITIONED_BYTES"),
            ("validation_mib", "VALIDATION_BYTES"),
        ):
            mib = self._positive_int(payload, field, 0, 1_048_576)
            if mib is not None:
                env[env_name] = str(mib * MIB)
        if vn_passes == 0:
            env["DIAGNOSTIC_VN_BYTES"] = "0"
        if conditioner == "none":
            env["CONDITIONED_BYTES"] = "0"

        slug = f"web-{profile}-{timestamp_slug()}-{job_id[:8]}"
        campaign_profiles = {
            "qualification", "cold-start", "dual-weave-lags", "dual-weave-stagger-qualification",
            "spatial-campaign", "lsb-campaign", "production-assessment", "final-preproduction", "global-all",
        }
        if profile in campaign_profiles:
            env["CAMPAIGN"] = slug
        else:
            env["RUN_NAME"] = slug
        output_root = self.settings.data_root / slug
        return env, slug, output_root

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            profile = str(payload.get("profile", "single"))
            if profile not in ALLOWED_PROFILES:
                raise ValueError("Nieobsługiwany profil testu")
            source_id = str(payload.get("source_id", ""))
            source = self.sources.get(source_id)
            if source is None:
                raise ValueError("Nieznane źródło")
            active = self.active_jobs()
            active_live = [j for j in active if str((j.get("source") or {}).get("source_type")) != "dataset-y"]
            active_dataset = [j for j in active if str((j.get("source") or {}).get("source_type")) == "dataset-y"]
            if source["source_type"] == "dataset-y":
                # Datasets are immutable/read-only inputs. They may run in parallel
                # with other dataset jobs and also alongside one active LIVE job.
                if len(active_dataset) >= self.max_dataset_jobs:
                    raise RuntimeError(f"Osiągnięto limit równoległych zadań dataset-y: {self.max_dataset_jobs}")
            elif active_live:
                # Exclusivity applies only between LIVE consumers of the camera.
                raise RuntimeError("Źródło LIVE jest już używane przez inny test")
            if profile == "final-preproduction" and source["source_type"] != "dataset-y":
                raise ValueError("Profil final-preproduction wymaga źródła dataset-y")

            job_id = uuid.uuid4().hex
            worker_port_span = 5 if profile == "final-preproduction" else 1
            worker_port = self._allocate_worker_port(worker_port_span)
            env, slug, output_root = self._build_environment(payload, source, profile, job_id, worker_port)
            script = (self.settings.root / ALLOWED_PROFILES[profile]).resolve()
            if not script.is_file():
                raise RuntimeError(f"Brak skryptu profilu: {script.name}")
            log_path = self._log_path(job_id)
            log_stream = log_path.open("ab", buffering=0)
            try:
                process = subprocess.Popen(
                    [str(script)], cwd=self.settings.root, env=env,
                    stdout=log_stream, stderr=subprocess.STDOUT, start_new_session=True,
                )
            except OSError as exc:
                log_stream.close()
                self.logger.exception("Nie udało się uruchomić profilu %s", profile)
                raise RuntimeError(f"Nie udało się uruchomić procesu: {exc}") from exc
            job = {
                "id": job_id, "slug": slug, "profile": profile,
                "source": redact_source(source), "status": "running",
                "created_at": utc_now(), "started_at": utc_now(), "ended_at": None,
                "pid": process.pid, "pgid": process.pid, "returncode": None,
                "worker_port": worker_port,
                "worker_port_span": worker_port_span,
                "worker_url": f"http://{self.settings.worker_host}:{worker_port}",
                "output_root": str(output_root), "log_file": str(log_path),
                "requested": {k: v for k, v in payload.items() if k != "csrf_token"},
                "message": "Test uruchomiony",
            }
            self._save_job(job)
            self.processes[job_id] = process
            threading.Thread(
                target=self._watch_process, args=(process, job_id, log_stream),
                name=f"job-{job_id[:8]}", daemon=True,
            ).start()
            self.logger.info("Uruchomiono job %s, pid=%s, port=%s, profile=%s", job_id, process.pid, worker_port, profile)
            return job

    def _watch_process(self, process: subprocess.Popen[bytes], job_id: str, log_stream: Any) -> None:
        returncode = process.wait()
        try:
            log_stream.close()
        except OSError:
            pass
        with self.lock:
            job = self._load_job(job_id) or {"id": job_id}
            previous = str(job.get("status", ""))
            job["returncode"] = returncode
            job["ended_at"] = utc_now()
            if previous == "stopping":
                job["status"] = "stopped"; job["message"] = "Test zatrzymany przez operatora"
            elif returncode == 0:
                job["status"] = "complete"; job["message"] = "Test i analizy zakończone"
            else:
                job["status"] = "failed"; job["message"] = f"Proces zakończył się kodem {returncode}"
            self._save_job(job)
            self.processes.pop(job_id, None)
            self.logger.info("Job %s zakończony rc=%s", job_id, returncode)

    def stop(self, job_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            job = self.current(job_id) if job_id else self.current()
            if job is None:
                raise RuntimeError("Brak aktywnego testu" if not job_id else "Nie znaleziono aktywnego testu")
            job["status"] = "stopping"
            job["message"] = "Wysyłanie SIGTERM do grupy procesów"
            self._save_job(job)
            pgid = int(job.get("pgid") or 0)
            if pgid <= 1:
                raise RuntimeError("Nieprawidłowy PGID aktywnego testu")
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            threading.Thread(
                target=self._force_kill_after_timeout,
                args=(str(job["id"]), pgid), daemon=True,
                name=f"stop-{str(job['id'])[:8]}",
            ).start()
            return job

    def _force_kill_after_timeout(self, job_id: str, pgid: int) -> None:
        time.sleep(20)
        with self.lock:
            job = self._load_job(job_id)
            if not job or job.get("status") != "stopping":
                return
            pid = int(job.get("pid") or 0)
            if not is_process_alive(pid):
                return
            try:
                os.killpg(pgid, signal.SIGKILL)
                job["message"] = "Proces nie zakończył się po SIGTERM; wysłano SIGKILL"
                self._save_job(job)
            except ProcessLookupError:
                pass

    def read_log(self, job_id: str, offset: int, limit: int = 256 * 1024) -> tuple[bytes, int, bool]:
        path = self._log_path(job_id)
        if not path.is_file():
            return b"", offset, False
        size = path.stat().st_size
        offset = min(max(offset, 0), size)
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        new_offset = offset + len(data)
        return data, new_offset, new_offset < size


class Auth:
    def __init__(self, app: Flask, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.username = ""
        self.password_hash = ""
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.failure_lock = threading.Lock()
        if settings.auth_mode == "app":
            credentials = read_json(settings.credentials_file)
            if not isinstance(credentials, dict):
                raise RuntimeError(f"Brak pliku poświadczeń WWW: {settings.credentials_file}")
            self.username = str(credentials.get("username", ""))
            self.password_hash = str(credentials.get("password_hash", ""))
            if not self.username or not self.password_hash:
                raise RuntimeError("Plik poświadczeń nie zawiera username/password_hash")

    def authenticated(self) -> bool:
        if self.settings.auth_mode == "none":
            return True
        if self.settings.auth_mode == "proxy":
            return bool(request.headers.get("X-Remote-User") or request.environ.get("REMOTE_USER"))
        return session.get("authenticated") is True and session.get("username") == self.username

    def csrf(self) -> str:
        value = session.get("csrf_token")
        if not isinstance(value, str) or len(value) < 32:
            value = secrets.token_urlsafe(32)
            session["csrf_token"] = value
        return value

    def check_csrf(self) -> bool:
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        return bool(expected and supplied and hmac.compare_digest(str(expected), str(supplied)))

    def login_allowed(self, address: str) -> bool:
        now = time.monotonic()
        with self.failure_lock:
            queue = self.failures[address]
            while queue and now - queue[0] > 600:
                queue.popleft()
            return len(queue) < 8

    def login_failed(self, address: str) -> None:
        with self.failure_lock:
            self.failures[address].append(time.monotonic())

    def login_succeeded(self, address: str) -> None:
        with self.failure_lock:
            self.failures.pop(address, None)


def safe_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(part in {"..", ".control"} for part in relative_path.parts):
        abort(404)
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        abort(404)
    return target


def human_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def scan_data_root(root: Path, limit: int = 200) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        # Raw frame datasets and their live/latest symlinks are input sources,
        # not entropy test runs. Keep them out of the reports dashboard.
        if path.name.startswith("frame-buffer-"):
            continue
        ready = path / "READY.json"
        failed = path / "run_failed.json"
        qualification = path / "qualification_report.html"
        dual_campaign = path / "dual_weave_campaign_report.html"
        spatial_campaign = path / "spatial_campaign_report.html"
        lsb_campaign = path / "lsb_campaign_report.html"
        global_campaign = path / "global_campaign_report.html"
        production_assessment = path / "production_assessment_report.html"
        final_preproduction = path / "final_preproduction_report.html"
        report = path / "run_report.html"
        status = "running/incomplete"
        if failed.exists():
            status = "failed"
        elif qualification.exists() or dual_campaign.exists() or spatial_campaign.exists() or lsb_campaign.exists() or production_assessment.exists() or final_preproduction.exists() or global_campaign.exists():
            status = "complete"
        elif ready.exists():
            status = "ready"
        elif (path / "output_complete.json").exists():
            status = "analyzing"
        report_url = (
            f"/data/{path.name}/final_preproduction_report.html"
            if final_preproduction.exists()
            else f"/data/{path.name}/production_assessment_report.html"
            if production_assessment.exists()
            else f"/data/{path.name}/global_campaign_report.html"
            if global_campaign.exists()
            else f"/data/{path.name}/lsb_campaign_report.html"
            if lsb_campaign.exists()
            else f"/data/{path.name}/qualification_report.html"
            if qualification.exists()
            else f"/data/{path.name}/dual_weave_campaign_report.html"
            if dual_campaign.exists()
            else f"/data/{path.name}/spatial_campaign_report.html"
            if spatial_campaign.exists()
            else f"/data/{path.name}/run_report.html"
            if report.exists()
            else None
        )
        report_label = (
            "Final preproduction" if final_preproduction.exists() else
            "Production assessment" if production_assessment.exists() else
            "Global campaign" if global_campaign.exists() else
            "LSB campaign" if lsb_campaign.exists() else
            "Kwalifikacja" if qualification.exists() else
            "Dual weave campaign" if dual_campaign.exists() else
            "Spatial campaign" if spatial_campaign.exists() else
            "Raport przebiegu" if report.exists() else "Brak raportu"
        )
        headline = ""
        if final_preproduction.exists():
            assessment = read_json_object(path / "final_preproduction_summary.json")
            decision = assessment.get("decision", {}) if isinstance(assessment.get("decision"), dict) else {}
            headline = f"production ready: {'YES' if decision.get('production_ready') else 'NO'} · {decision.get('production_candidate') or '—'}"
        elif production_assessment.exists():
            assessment = read_json_object(path / "production_assessment_summary.json")
            status_summary = assessment.get("status", {}) if isinstance(assessment.get("status"), dict) else {}
            headline = (
                f"complete {status_summary.get('complete', '—')}/{status_summary.get('total', '—')} · "
                f"failed {status_summary.get('failed', '—')} · health {status_summary.get('health_failures', '—')}"
            )
        elif global_campaign.exists():
            campaign_summary = read_json_object(path / "global_campaign_summary.json")
            steps = campaign_summary.get("steps", []) if isinstance(campaign_summary.get("steps"), list) else []
            headline = (
                f"passed {campaign_summary.get('passed', '—')}/{len(steps) or '—'} · "
                f"failed {campaign_summary.get('failed', '—')} · skipped {campaign_summary.get('skipped', '—')}"
            )
        elif lsb_campaign.exists():
            campaign_summary = read_json_object(path / "lsb_campaign_summary.json")
            profiles = campaign_summary.get("profiles", []) if isinstance(campaign_summary.get("profiles"), list) else []
            headline = (
                f"complete {campaign_summary.get('complete', '—')}/{len(profiles) or '—'} · "
                f"failed {campaign_summary.get('failed', '—')}"
            )
        elif spatial_campaign.exists():
            campaign_summary = read_json_object(path / "spatial_campaign_summary.json")
            completed = campaign_summary.get("complete_profiles")
            total = campaign_summary.get("total_profiles")
            failed_profiles = campaign_summary.get("failed_profiles")
            if completed is not None:
                headline = f"complete {completed}/{total if total is not None else '—'} · failed {failed_profiles if failed_profiles is not None else '—'}"
        elif dual_campaign.exists():
            campaign_summary = read_json_object(path / "dual_weave_campaign_summary.json")
            best = campaign_summary.get("diagnostic_best", {}) if isinstance(campaign_summary.get("diagnostic_best"), dict) else {}
            if best:
                headline = (
                    f"{best.get('order', '—')} / {best.get('alignment', '—')} · "
                    f"{float(best.get('throughput_ratio_mean') or 0):.3f}× baseline · "
                    f"max |φ| {float(best.get('positional_abs_phi_max') or 0):.6g}"
                )
        elif qualification.exists():
            qualification_summary = read_json_object(path / "qualification_summary.json")
            complete_runs = qualification_summary.get("complete_runs")
            latched_runs = qualification_summary.get("latched_runs")
            if complete_runs is None and isinstance(qualification_summary.get("runs"), list):
                complete_runs = sum(1 for item in qualification_summary["runs"] if isinstance(item, dict) and item.get("status") == "complete")
            if complete_runs is not None:
                headline = f"complete {complete_runs} · latches {latched_runs if latched_runs is not None else '—'}"
        elif report.exists():
            dual_summary = read_json_object(path / "dual_weave_report.json")
            best = dual_summary.get("diagnostic_best", {}) if isinstance(dual_summary.get("diagnostic_best"), dict) else {}
            if best:
                headline = (
                    f"{best.get('order', '—')} / {best.get('alignment', '—')} · "
                    f"{float(best.get('throughput_ratio_vs_checkerboard') or 0):.3f}× baseline · "
                    f"|φ| {float(best.get('conditioner_input_positional_worst_abs_phi') or 0):.6g}"
                )
        rows.append(
            {
                "name": path.name,
                "mtime": path.stat().st_mtime,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "status": status,
                "report": report_url,
                "report_label": report_label,
                "headline": headline,
                "url": f"/data/{path.name}/",
            }
        )
    rows.sort(key=lambda item: float(item["mtime"]), reverse=True)
    return rows[:limit]


def proxy_worker(settings: Settings, worker_port: int, path: str) -> Response:
    url = f"http://{settings.worker_host}:{worker_port}/{path.lstrip('/')}"
    if request.query_string:
        url += "?" + request.query_string.decode("ascii", errors="ignore")
    try:
        upstream = urllib.request.urlopen(url, timeout=4)
        body = upstream.read()
        headers = {}
        content_type = upstream.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        headers["Cache-Control"] = "no-store"
        return Response(body, status=upstream.status, headers=headers)
    except urllib.error.HTTPError as exc:
        return Response(exc.read(), status=exc.code, content_type=exc.headers.get("Content-Type", "text/plain"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        return jsonify({"error": "worker_unavailable", "details": str(exc)}), 503


def create_app(settings: Settings) -> Flask:
    settings.data_root.mkdir(parents=True, exist_ok=True)
    secret = settings.secret_file.read_bytes().strip()
    if len(secret) < 32:
        raise RuntimeError(f"Sekret Flask musi mieć co najmniej 32 bajty: {settings.secret_file}")
    sources = load_sources(settings.sources_file)
    app = Flask(__name__, template_folder=str(TEMPLATES_ROOT), static_folder=str(STATIC_ROOT), static_url_path="/static")
    app.secret_key = secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=settings.secure_cookie,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if settings.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)  # type: ignore[method-assign]

    logger = logging.getLogger("camera_entropy_control")
    manager = JobManager(settings, sources, logger)
    auth = Auth(app, settings)
    share_token = ""
    if settings.share_token_file is not None and settings.share_token_file.is_file():
        share_token = settings.share_token_file.read_text(encoding="utf-8").strip()
        if len(share_token) < 32:
            raise RuntimeError("Token read-only share musi mieć co najmniej 32 znaki")

    def valid_share_token(token: str) -> bool:
        return bool(share_token and token and hmac.compare_digest(share_token, token))

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; base-uri 'self'",
        )
        return response

    @app.before_request
    def access_control() -> Response | None:
        if request.endpoint in {"login", "healthz", "static"}:
            return None
        if request.endpoint in {"share_index", "share_data", "share_api_runs", "share_api_latest"}:
            token = str((request.view_args or {}).get("token", ""))
            if valid_share_token(token):
                return None
            abort(404)
        if not auth.authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication_required"}), 401
            return redirect(url_for("login", next=request.full_path))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not auth.check_csrf():
            return jsonify({"error": "csrf_failed"}), 403
        auth.csrf()
        return None

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok", "version": APP_VERSION})

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Response | str:
        if settings.auth_mode != "app":
            return redirect(url_for("dashboard"))
        error = ""
        if request.method == "POST":
            address = request.remote_addr or "unknown"
            if not auth.login_allowed(address):
                return Response("Zbyt wiele nieudanych prób logowania", status=429, content_type="text/plain; charset=utf-8")
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if hmac.compare_digest(username, auth.username) and check_password_hash(auth.password_hash, password):
                auth.login_succeeded(address)
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                session["username"] = auth.username
                auth.csrf()
                destination = request.args.get("next") or url_for("dashboard")
                if not destination.startswith("/") or destination.startswith("//"):
                    destination = url_for("dashboard")
                return redirect(destination)
            auth.login_failed(address)
            time.sleep(0.35)
            error = "Nieprawidłowy login lub hasło"
        return render_template("login.html", error=error, version=APP_VERSION)

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def dashboard() -> str:
        return render_template(
            "control.html",
            version=APP_VERSION,
            csrf_token=auth.csrf(),
            sources=[redact_source(item) for item in sources],
            default_source_id=("buffered-latest" if any(item.get("id") == "buffered-latest" for item in sources) else str(sources[0]["id"])),
            profiles=profile_rows(),
            parameter_help=PARAMETER_HELP,
            worker_port=settings.worker_port,
            share_url=(f"/share/{share_token}/" if share_token else None),
        )

    @app.get("/api/control/status")
    def api_control_status() -> Response:
        active = manager.active_jobs()
        current = active[0] if active else None
        jobs = manager.list_jobs(30)
        dataset_active = sum(1 for item in active if str((item.get("source") or {}).get("source_type")) == "dataset-y")
        return jsonify(
            {
                "version": APP_VERSION,
                "current": current,
                "active": active,
                "active_count": len(active),
                "dataset_active": dataset_active,
                "max_dataset_jobs": manager.max_dataset_jobs,
                "jobs": jobs,
                "runs": scan_data_root(settings.data_root, 30),
                "worker_available": bool(active),
                "csrf_token": auth.csrf(),
            }
        )

    @app.post("/api/jobs/start")
    def api_start_job() -> Response:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid_json"}), 400
        try:
            job = manager.start(payload)
            return jsonify({"ok": True, "job": job}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as exc:  # defensive boundary for the public control API
            logger.exception("Nieoczekiwany błąd podczas uruchamiania joba")
            return jsonify({"error": f"Nieoczekiwany błąd startu: {type(exc).__name__}: {exc}"}), 500

    @app.post("/api/jobs/stop")
    def api_stop_job() -> Response:
        try:
            job = manager.stop()
            return jsonify({"ok": True, "job": job})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409

    @app.post("/api/jobs/<job_id>/stop")
    def api_stop_specific_job(job_id: str) -> Response:
        if not job_id.isalnum() or len(job_id) > 64:
            abort(404)
        try:
            job = manager.stop(job_id)
            return jsonify({"ok": True, "job": job})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409

    @app.get("/api/jobs/<job_id>/log")
    def api_job_log(job_id: str) -> Response:
        if not job_id.isalnum() or len(job_id) > 64:
            abort(404)
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            offset = 0
        data, new_offset, more = manager.read_log(job_id, offset)
        return jsonify(
            {
                "offset": new_offset,
                "more": more,
                "text": data.decode("utf-8", errors="replace"),
            }
        )

    @app.get("/api/runs")
    def api_runs() -> Response:
        return jsonify({"runs": scan_data_root(settings.data_root)})

    def selected_preview_job() -> dict[str, Any] | None:
        requested = str(request.args.get("job", "")).strip()
        if requested:
            job = manager.current(requested)
            if job is not None:
                session["preview_job_id"] = requested
                return job
        remembered = str(session.get("preview_job_id", ""))
        if remembered:
            job = manager.current(remembered)
            if job is not None:
                return job
        return manager.current()

    @app.get("/live")
    def live() -> Response:
        job = selected_preview_job()
        if job is None:
            return Response("<h1>Brak aktywnego workera</h1><p><a href='/'>Wróć do panelu</a></p>", 503, content_type="text/html; charset=utf-8")
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), "/")

    # Compatibility/proxy endpoints expected by the compute worker's live UI.
    @app.get("/api/stats")
    @app.get("/api/status")
    def worker_stats() -> Response:
        job = selected_preview_job()
        if job is None:
            return jsonify({"error": "worker_unavailable"}), 503
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), request.path)

    @app.get("/api/mask-drift-history")
    def worker_mask_history() -> Response:
        job = selected_preview_job()
        if job is None:
            return jsonify({"error": "worker_unavailable"}), 503
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), request.path)

    @app.get("/api/byte-diagnostics")
    def worker_byte_diagnostics() -> Response:
        job = selected_preview_job()
        if job is None:
            return jsonify({"error": "worker_unavailable"}), 503
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), request.path)

    @app.get("/worker-health")
    def worker_health() -> Response:
        job = selected_preview_job()
        if job is None:
            return jsonify({"error": "worker_unavailable"}), 503
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), "/health")

    def proxy_asset(path: str) -> Response:
        job = selected_preview_job()
        if job is None:
            return jsonify({"error": "worker_unavailable"}), 503
        return proxy_worker(settings, int(job.get("worker_port") or settings.worker_port), path)

    for endpoint in (
        "frame.jpg", "y.png", "lsb_change.png", "mask_active.png", "mask.png",
        "mask_shadow.png", "mask_difference.png", "mask_active_overlay.png",
        "mask_overlay.png", "mask_shadow_overlay.png", "live_byte_heatmaps.png",
    ):
        app.add_url_rule(
            f"/{endpoint}", endpoint=f"proxy_{endpoint.replace('.', '_')}",
            view_func=(lambda path=endpoint: proxy_asset("/" + path)), methods=["GET"],
        )

    @app.get("/download/<path:name>")
    def worker_download(name: str) -> Response:
        return proxy_asset("/download/" + name)


    @app.get("/share/<token>/")
    def share_index(token: str) -> str:
        rows = scan_data_root(settings.data_root)
        return render_template("share_index.html", rows=rows, token=token, version=APP_VERSION)

    @app.get("/share/<token>/api/runs")
    def share_api_runs(token: str) -> Response:
        return jsonify({"version": APP_VERSION, "runs": scan_data_root(settings.data_root)})

    @app.get("/share/<token>/api/latest")
    def share_api_latest(token: str) -> Response:
        rows = scan_data_root(settings.data_root, 1)
        if not rows:
            return jsonify({"error": "no_runs"}), 404
        row = rows[0]
        root = settings.data_root / str(row["name"])
        payload: dict[str, Any] = {"version": APP_VERSION, "run": row}
        for filename, key in (
            ("qualification_summary.json", "qualification"),
            ("dual_weave_report.json", "dual_weave"),
            ("dual_weave_campaign_summary.json", "dual_weave_campaign"),
            ("binary_geometry_summary.json", "binary_geometry"),
            ("runner_summary.json", "runner"),
            ("READY.json", "ready"),
            ("run_failed.json", "failure"),
        ):
            value = read_json(root / filename)
            if isinstance(value, dict):
                payload[key] = value
        payload["browse_url"] = f"/share/{token}/data/{row['name']}/"
        return jsonify(payload)

    @app.get("/share/<token>/data/<path:relative>")
    def share_data(token: str, relative: str) -> Response | str:
        target = safe_path(settings.data_root, relative)
        if not target.exists():
            abort(404)
        if target.is_file():
            suffix = target.suffix.lower()
            inline = suffix in {".html", ".htm", ".json", ".csv", ".txt", ".log", ".md", ".png", ".jpg", ".jpeg", ".svg", ".webp"}
            return send_file(target, mimetype=mimetypes.guess_type(target.name)[0], as_attachment=not inline, download_name=target.name, conditional=True, etag=True, max_age=0)
        entries=[]
        for item in target.iterdir():
            if item.name.startswith(".") or item.is_symlink():
                continue
            rel=item.relative_to(settings.data_root).as_posix()
            stat=item.stat()
            entries.append({"name":item.name,"is_dir":item.is_dir(),"size":"—" if item.is_dir() else human_bytes(stat.st_size),"modified":datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat(timespec="seconds"),"url":f"/share/{token}/data/{rel}/" if item.is_dir() else f"/share/{token}/data/{rel}"})
        entries.sort(key=lambda row:(not row["is_dir"],row["name"].lower()))
        return render_template("share_data_index.html", entries=entries, token=token, relative=relative, version=APP_VERSION)

    @app.get("/data/")
    @app.get("/data/<path:relative>")
    def data_browser(relative: str = "") -> Response | str:
        target = safe_path(settings.data_root, relative)
        if not target.exists():
            abort(404)
        if target.is_file():
            suffix = target.suffix.lower()
            inline = suffix in {
                ".html", ".htm", ".json", ".csv", ".txt", ".log", ".md",
                ".png", ".jpg", ".jpeg", ".svg", ".webp",
            }
            guessed = mimetypes.guess_type(target.name)[0]
            return send_file(
                target,
                mimetype=guessed,
                as_attachment=not inline,
                download_name=target.name,
                conditional=True,
                etag=True,
                max_age=0,
            )
        entries: list[dict[str, Any]] = []
        for item in target.iterdir():
            if item.name.startswith(".") or item.is_symlink():
                continue
            rel = item.relative_to(settings.data_root).as_posix()
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": "—" if item.is_dir() else human_bytes(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    "url": f"/data/{rel}/" if item.is_dir() else f"/data/{rel}",
                }
            )
        entries.sort(key=lambda row: (not row["is_dir"], row["name"].lower()))
        crumbs = [{"name": "data", "url": "/data/"}]
        current = Path()
        for part in Path(relative).parts:
            current /= part
            crumbs.append({"name": part, "url": f"/data/{current.as_posix()}/"})
        return render_template(
            "data_index.html",
            entries=entries,
            relative=relative,
            crumbs=crumbs,
            runs=scan_data_root(settings.data_root, 100) if not relative else [],
            csrf_token=auth.csrf(),
        )

    register_documentation_routes(app, settings.root)
    return app


def parse_args() -> Settings:
    root = PROJECT_ROOT
    parser = argparse.ArgumentParser(description="Persistent Camera Entropy web control plane")
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8087")))
    parser.add_argument("--worker-host", default=os.environ.get("WORKER_HOST", "127.0.0.1"))
    parser.add_argument("--worker-port", type=int, default=int(os.environ.get("WORKER_PORT", "18087")))
    parser.add_argument("--max-dataset-jobs", type=int, default=int(os.environ.get("MAX_DATASET_JOBS", str(min(16, os.cpu_count() or 4)))))
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("DATA_ROOT", root / "data")))
    parser.add_argument("--sources-file", type=Path, default=Path(os.environ.get("WEB_SOURCES_FILE", root / "sources.json")))
    parser.add_argument("--credentials-file", type=Path, default=Path(os.environ.get("WEB_CREDENTIALS_FILE", "/etc/camera-entropy/web-user.json")))
    parser.add_argument("--secret-file", type=Path, default=Path(os.environ.get("WEB_SECRET_FILE", "/etc/camera-entropy/web-secret.key")))
    parser.add_argument("--auth-mode", choices=("app", "proxy", "none"), default=os.environ.get("WEB_AUTH_MODE", "app"))
    parser.add_argument("--secure-cookie", action=argparse.BooleanOptionalAction, default=os.environ.get("WEB_SECURE_COOKIE", "1") != "0")
    parser.add_argument("--trust-proxy", action=argparse.BooleanOptionalAction, default=os.environ.get("WEB_TRUST_PROXY", "1") != "0")
    parser.add_argument("--share-token-file", type=Path, default=Path(os.environ["WEB_SHARE_TOKEN_FILE"]) if os.environ.get("WEB_SHARE_TOKEN_FILE") else None)
    args = parser.parse_args()
    if args.max_dataset_jobs < 1 or args.max_dataset_jobs > 256:
        parser.error("max-dataset-jobs musi być w zakresie 1..256")
    os.environ["MAX_DATASET_JOBS"] = str(args.max_dataset_jobs)
    if args.port == args.worker_port and args.host == args.worker_host:
        parser.error("Port control servera i workera nie może być taki sam")
    if args.auth_mode == "none" and args.host not in {"127.0.0.1", "::1", "localhost"} and os.environ.get("ALLOW_INSECURE_WEB") != "1":
        parser.error("auth-mode=none poza loopback wymaga ALLOW_INSECURE_WEB=1")
    return Settings(
        root=root,
        data_root=args.data_root.resolve(),
        sources_file=args.sources_file.resolve(),
        credentials_file=args.credentials_file.resolve(),
        secret_file=args.secret_file.resolve(),
        host=args.host,
        port=args.port,
        worker_host=args.worker_host,
        worker_port=args.worker_port,
        auth_mode=args.auth_mode,
        secure_cookie=args.secure_cookie,
        trust_proxy=args.trust_proxy,
        share_token_file=args.share_token_file.resolve() if args.share_token_file else None,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )
    settings = parse_args()
    app = create_app(settings)
    logging.getLogger("camera_entropy_control").info(
        "Start %s na %s:%s; worker=%s:%s; data=%s",
        APP_VERSION,
        settings.host,
        settings.port,
        settings.worker_host,
        settings.worker_port,
        settings.data_root,
    )
    app.run(host=settings.host, port=settings.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
