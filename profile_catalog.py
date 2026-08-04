#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for control-panel profiles and their UI presets.

The execution script, label, help text, category and visible form preset live in
one module.  The control API still validates every submitted value; the browser
preset is convenience and transparency, not a security boundary.
"""
from __future__ import annotations

from typing import Any

PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "final-preproduction": {
        "script": "qualification_final_preproduction.py",
        "label": "PREPRODUCTION FINAL — pełny dataset i kandydaci produkcyjni",
        "category": "Preprodukcja",
        "help": (
            "Ostateczna kwalifikacja przedprodukcyjna. Dla dataset-y wykorzystuje cały "
            "aktualnie dostępny dataset, uruchamia równolegle bezpieczny kandydat XOR/1 LSB "
            "oraz kontrolne warianty optymalizacyjne i generuje jeden raport zbiorczy."
        ),
        "preset": {
            "dataset_scope": "all-available",
            "dataset_verify_hashes": True,
            "dataset_verify_workers": 8,
            "dataset_verify_progress_seconds": 2,
            "final_preprod_workers": 5,
            "final_preprod_status_interval_seconds": 30,
            "final_preprod_verify_cache": True,
            "sample_mode": "xor",
            "lsb_bits": 1,
            "pairing_mode": "disjoint",
            "pair_lag_frames": 4,
            "spatial_mask_pattern": "full",
            "spatial_sampling": "full",
            "serialization_order": "row-major",
            "entropy_credit_bits_per_pixel": 0.5,
            "von_neumann_passes": 0,
            "conditioner": "sha3-512",
            "conditioner_input_bits": 2048,
            "conditioned_mib": 64,
            "diagnostic_vn_mib": 0,
            "validation_mib": 64,
            "warmup_seconds": 0,
            "calibration_pairs": 512,
            "stream_stats_window_pairs": 1024,
            "web_images": False,
            "mask_snapshot_images": False,
            "live_byte_diagnostics": False,
        },
    },
    "production-assessment": {
        "script": "qualification_production_assessment.py",
        "label": "PRODUCTION — kompleksowa macierz decyzyjna",
        "category": "Preprodukcja",
        "help": (
            "Faktoryzowana macierz porównawcza 1–4 LSB, pairingu, lagów, masek, "
            "serializacji, entropy credit, wejścia SHA3 i Dual Weave."
        ),
        "preset": {
            "dataset_scope": "output-limit",
            "assessment_level": "full",
            "web_images": False,
            "mask_snapshot_images": False,
            "live_byte_diagnostics": False,
            "von_neumann_passes": 0,
        },
    },
    "qualification": {
        "script": "qualification_preproduction.sh",
        "label": "Kwalifikacja — powtarzalne przebiegi",
        "category": "Preprodukcja",
        "help": "Wielokrotny przebieg kwalifikacyjny i raport powtarzalności.",
        "preset": {"runs": 3, "conditioned_mib": 25, "diagnostic_vn_mib": 25, "validation_mib": 25},
    },
    "cold-start": {
        "script": "qualification_cold_start_run.sh",
        "label": "Kwalifikacja po zimnym starcie",
        "category": "Preprodukcja",
        "help": "Kwalifikacja obejmująca pełny warm-up po starcie źródła.",
        "preset": {"runs": 3, "first_warmup_seconds": 1800, "next_warmup_seconds": 0},
    },
    "single": {
        "script": "run_one.sh",
        "label": "Pojedynczy przebieg — konfiguracja ręczna",
        "category": "Podstawowe",
        "help": "Jeden przebieg z parametrami ustawionymi w formularzu.",
        "preset": {},
    },
    "temporal-sha3": {
        "script": "smoke_temporal_sha3.sh",
        "label": "Temporal XOR → SHA3-512 — uproszczony tor",
        "category": "Podstawowe",
        "help": "Temporalny XOR bez VN, porównań przestrzennych i Dual Weave.",
        "preset": {
            "sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint", "pair_lag_frames": 4,
            "spatial_mask_pattern": "full", "spatial_sampling": "full", "serialization_order": "row-major",
            "von_neumann_passes": 0, "conditioner": "sha3-512", "conditioner_input_bits": 2048,
            "conditioned_mib": 1, "diagnostic_vn_mib": 0, "validation_mib": 8,
        },
    },
    "smoke": {
        "script": "smoke_preproduction.sh",
        "label": "Smoke — VN + SHA3-512",
        "category": "Podstawowe",
        "help": "Krótki test klasycznego toru z diagnostycznym Von Neumannem.",
        "preset": {"von_neumann_passes": 1, "conditioned_mib": 5, "diagnostic_vn_mib": 5, "validation_mib": 5},
    },
    "lsb-campaign": {
        "script": "smoke_lsb_profiles.sh",
        "label": "LSB — kampania 1..4 bitów",
        "category": "Kampanie",
        "help": "Porównuje temporal XOR, direct Y i delta dla 1–4 dolnych bitów.",
        "preset": {"dataset_scope": "output-limit", "von_neumann_passes": 0, "web_images": False, "live_byte_diagnostics": False},
    },
    "spatial-campaign": {
        "script": "smoke_spatial_profiles.sh",
        "label": "Spatial — pełna kampania porównawcza",
        "category": "Kampanie",
        "help": "Porównuje wszystkie publiczne profile masek i serializacji.",
        "preset": {"sample_mode": "xor", "lsb_bits": 1, "conditioner_input_bits": 2048},
    },
    "dual-weave-lags": {
        "script": "smoke_dual_weave_lags.sh",
        "label": "Dual Weave — kampania lagów",
        "category": "Kampanie",
        "help": "Porównuje warianty Dual Weave dla wielu lagów.",
        "preset": {"sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint", "spatial_sampling": "checkerboard-even"},
    },
    "global-all": {
        "script": "qualification_global_all_profiles.sh",
        "label": "GLOBAL — wszystkie profile i kampanie",
        "category": "Kampanie",
        "help": "Uruchamia historyczne profile i buduje raport globalny.",
        "preset": {"assessment_level": "full", "web_images": False, "live_byte_diagnostics": False},
    },
    "spatial-phases": {
        "script": "smoke_checkerboard_phases.sh", "label": "Spatial — checkerboard phases smoke", "category": "Maski i geometria",
        "help": "Porównanie obu faz szachownicy.", "preset": {"spatial_mask_pattern": "legacy", "spatial_sampling": "checkerboard-even"},
    },
    "spatial-baseline": {
        "script": "smoke_spatial_baseline.sh", "label": "Spatial — baseline full / row-major", "category": "Maski i geometria",
        "help": "Pełna zamrożona maska i row-major.", "preset": {"spatial_mask_pattern": "full", "serialization_order": "row-major"},
    },
    "spatial-checker-even": {
        "script": "smoke_spatial_checkerboard_even.sh", "label": "Spatial — checkerboard even", "category": "Maski i geometria",
        "help": "Parzysta faza szachownicy.", "preset": {"spatial_mask_pattern": "checkerboard-even", "spatial_sampling": "checkerboard-even"},
    },
    "spatial-checker-odd": {
        "script": "smoke_spatial_checkerboard_odd.sh", "label": "Spatial — checkerboard odd", "category": "Maski i geometria",
        "help": "Nieparzysta faza szachownicy.", "preset": {"spatial_mask_pattern": "checkerboard-odd", "spatial_sampling": "checkerboard-odd"},
    },
    "spatial-grid2": {
        "script": "smoke_spatial_grid_2x2.sh", "label": "Spatial — grid 2×2", "category": "Maski i geometria",
        "help": "Jedna klasa pikseli modulo 2×2.", "preset": {"spatial_mask_pattern": "grid", "spatial_step_x": 2, "spatial_step_y": 2, "spatial_phase_x": 0, "spatial_phase_y": 0},
    },
    "spatial-grid4": {
        "script": "smoke_spatial_grid_4x4.sh", "label": "Spatial — grid 4×4", "category": "Maski i geometria",
        "help": "Jedna klasa pikseli modulo 4×4.", "preset": {"spatial_mask_pattern": "grid", "spatial_step_x": 4, "spatial_step_y": 4, "spatial_phase_x": 0, "spatial_phase_y": 0},
    },
    "spatial-block4": {
        "script": "smoke_spatial_block_4x4_phase_1_2.sh", "label": "Spatial — block 4×4, faza (1,2)", "category": "Maski i geometria",
        "help": "Jedna lokalna pozycja w każdym bloku 4×4.", "preset": {"spatial_mask_pattern": "block", "spatial_block_width": 4, "spatial_block_height": 4, "spatial_phase_x": 1, "spatial_phase_y": 2},
    },
    "spatial-offset11": {
        "script": "smoke_spatial_offset_diagonal_1.sh", "label": "Spatial — offset XOR (+1,+1)", "category": "Maski i geometria",
        "help": "Porównanie z przesuniętym pikselem starszej klatki.", "preset": {"spatial_mask_pattern": "full", "temporal_spatial_offset_x": 1, "temporal_spatial_offset_y": 1},
    },
    "spatial-offset22": {
        "script": "smoke_spatial_offset_diagonal_2.sh", "label": "Spatial — offset XOR (+2,+2)", "category": "Maski i geometria",
        "help": "Porównanie z przesunięciem +2,+2.", "preset": {"spatial_mask_pattern": "full", "temporal_spatial_offset_x": 2, "temporal_spatial_offset_y": 2},
    },
    "spatial-serpentine": {
        "script": "smoke_spatial_serpentine.sh", "label": "Spatial — serializacja serpentine", "category": "Maski i geometria",
        "help": "Naprzemienny kierunek wierszy.", "preset": {"spatial_mask_pattern": "full", "serialization_order": "serpentine"},
    },
    "spatial-tile16": {
        "script": "smoke_spatial_tile_interleave_16.sh", "label": "Spatial — tile interleave 16×16", "category": "Maski i geometria",
        "help": "Serializacja pozycji lokalnych pomiędzy kaflami 16×16.", "preset": {"spatial_mask_pattern": "full", "serialization_order": "tile-interleave", "serialization_tile_width": 16, "serialization_tile_height": 16},
    },
    "dual-weave": {
        "script": "smoke_dual_weave.sh", "label": "Dual Weave — równoległy smoke", "category": "Eksperymentalne",
        "help": "Eksperymentalne splatanie faz A/B.", "preset": {"sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint", "spatial_sampling": "checkerboard-even"},
    },
    "dual-weave-stagger2": {
        "script": "smoke_dual_weave_stagger2.sh", "label": "Dual Weave — row-major / stagger-2", "category": "Eksperymentalne",
        "help": "Dual Weave z przesunięciem dwóch grup.", "preset": {"sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint", "spatial_sampling": "checkerboard-even", "conditioner_input_bits": 2048},
    },
    "dual-weave-stagger-qualification": {
        "script": "qualification_dual_weave_stagger.sh", "label": "Dual Weave — kwalifikacja stagger-2", "category": "Eksperymentalne",
        "help": "Wielokrotna kwalifikacja Dual Weave stagger-2.", "preset": {"sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint"},
    },
}

ALLOWED_PROFILES = {key: value["script"] for key, value in PROFILE_DEFINITIONS.items()}


def profile_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": value["label"],
            "category": value["category"],
            "help": value.get("help", ""),
            "preset": value.get("preset", {}),
        }
        for key, value in PROFILE_DEFINITIONS.items()
    ]
