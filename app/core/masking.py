#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calibration and health monitoring for frozen and shadow pixel masks.

This module contains only mask-learning logic.  Camera transport, sample
extraction, serialization and Flask presentation deliberately live elsewhere.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


class FrozenPixelCalibrator:
    """Learn per-pixel behavior for N frame pairs, then freeze the active mask."""

    def __init__(self, shape: tuple[int, int], target_pairs: int, args: argparse.Namespace) -> None:
        self.shape = shape
        self.target = target_pairs
        self.args = args
        self.pairs = 0
        self.ones = np.zeros(shape, dtype=np.uint32)
        self.transitions = np.zeros(shape, dtype=np.uint32)
        self.clips = np.zeros(shape, dtype=np.uint32)
        self.previous_change: Optional[np.ndarray] = None
        self.mask: Optional[np.ndarray] = None
        self.reason_code: Optional[np.ndarray] = None
        self.p1_rate: Optional[np.ndarray] = None
        self.transition_rate: Optional[np.ndarray] = None
        self.clip_rate: Optional[np.ndarray] = None

    @property
    def ready(self) -> bool:
        return self.mask is not None

    def update(self, change: np.ndarray, y: np.ndarray, previous_y: np.ndarray) -> None:
        if self.ready:
            return
        self.ones += change
        if self.previous_change is not None:
            self.transitions += np.not_equal(change, self.previous_change)
        clipped = (
            (y <= self.args.clip_low)
            | (y >= self.args.clip_high)
            | (previous_y <= self.args.clip_low)
            | (previous_y >= self.args.clip_high)
        )
        self.clips += clipped
        self.previous_change = change.copy()
        self.pairs += 1
        if self.pairs >= self.target:
            self.freeze()

    def freeze(self) -> None:
        self.p1_rate = self.ones.astype(np.float32) / max(1, self.pairs)
        self.transition_rate = self.transitions.astype(np.float32) / max(1, self.pairs - 1)
        self.clip_rate = self.clips.astype(np.float32) / max(1, self.pairs)

        p_ok = (self.p1_rate >= self.args.mask_p1_min) & (self.p1_rate <= self.args.mask_p1_max)
        transition_ok = (
            (self.transition_rate >= self.args.mask_transition_min)
            & (self.transition_rate <= self.args.mask_transition_max)
        )
        clip_ok = self.clip_rate <= self.args.mask_clip_max
        self.mask = p_ok & transition_ok & clip_ok

        reason = np.zeros(self.shape, dtype=np.uint8)
        reason[~p_ok] |= 1
        reason[~transition_ok] |= 2
        reason[~clip_ok] |= 4
        self.reason_code = reason

    def report(self) -> dict[str, Any]:
        eligible = int(self.mask.sum()) if self.mask is not None else 0
        total = int(np.prod(self.shape))
        return {
            "pairs": self.pairs,
            "target": self.target,
            "ready": self.ready,
            "eligible_pixels": eligible,
            "eligible_rate": eligible / total if total else 0.0,
        }


@dataclass
class MaskComparison:
    active_pixels: int = 0
    shadow_pixels: int = 0
    overlap_pixels: int = 0
    union_pixels: int = 0
    active_retention: float = 0.0
    shadow_retention: float = 0.0
    jaccard: float = 0.0
    disagreement_rate: float = 0.0
    updates: int = 0
    grace_remaining: int = 0
    bad_streak: int = 0
    bad: bool = False
    latched: bool = False
    details: str = ""


class ShadowPixelMonitor:
    """Continuously track a dynamic mask without using it to select output."""

    def __init__(
        self,
        active_mask: np.ndarray,
        initial_p1: np.ndarray,
        initial_transition: np.ndarray,
        initial_clip: np.ndarray,
        previous_change: np.ndarray,
        args: argparse.Namespace,
    ) -> None:
        self.active_mask = active_mask
        self.args = args
        self.alpha = 1.0 - math.pow(0.5, 1.0 / args.shadow_half_life_pairs)
        self.p1 = initial_p1.astype(np.float32, copy=True)
        self.transition = initial_transition.astype(np.float32, copy=True)
        self.clip = initial_clip.astype(np.float32, copy=True)
        self.previous_change = previous_change.copy()
        self.mask = active_mask.copy()
        self.updates = 0
        self.bad_streak = 0
        self.latched = False
        self.last_details = ""
        self.comparison = self._compare()

    def _derive_mask(self) -> np.ndarray:
        p_ok = (self.p1 >= self.args.mask_p1_min) & (self.p1 <= self.args.mask_p1_max)
        transition_ok = (
            (self.transition >= self.args.mask_transition_min)
            & (self.transition <= self.args.mask_transition_max)
        )
        clip_ok = self.clip <= self.args.mask_clip_max
        return p_ok & transition_ok & clip_ok

    def _compare(self) -> MaskComparison:
        active = self.active_mask
        shadow = self.mask
        active_pixels = int(active.sum())
        shadow_pixels = int(shadow.sum())
        overlap = int(np.count_nonzero(active & shadow))
        union = int(np.count_nonzero(active | shadow))
        disagreement = int(np.count_nonzero(active ^ shadow))
        total = int(active.size)
        active_retention = overlap / active_pixels if active_pixels else 0.0
        shadow_retention = overlap / shadow_pixels if shadow_pixels else 0.0
        jaccard = overlap / union if union else 1.0
        grace_remaining = max(0, self.args.shadow_grace_pairs - self.updates)
        bad = (
            grace_remaining == 0
            and (
                active_retention < self.args.shadow_min_active_retention
                or jaccard < self.args.shadow_min_jaccard
            )
        )
        details = (
            f"active_retention={active_retention:.6f} "
            f"(min={self.args.shadow_min_active_retention:.6f}), "
            f"jaccard={jaccard:.6f} (min={self.args.shadow_min_jaccard:.6f}), "
            f"disagreement={disagreement / total if total else 0.0:.6f}"
        )
        return MaskComparison(
            active_pixels=active_pixels,
            shadow_pixels=shadow_pixels,
            overlap_pixels=overlap,
            union_pixels=union,
            active_retention=active_retention,
            shadow_retention=shadow_retention,
            jaccard=jaccard,
            disagreement_rate=disagreement / total if total else 0.0,
            updates=self.updates,
            grace_remaining=grace_remaining,
            bad_streak=self.bad_streak,
            bad=bad,
            latched=self.latched,
            details=details,
        )

    def update(self, change: np.ndarray, y: np.ndarray, previous_y: np.ndarray) -> tuple[MaskComparison, bool]:
        clipped = (
            (y <= self.args.clip_low)
            | (y >= self.args.clip_high)
            | (previous_y <= self.args.clip_low)
            | (previous_y >= self.args.clip_high)
        )
        transition_sample = np.not_equal(change, self.previous_change)
        alpha = np.float32(self.alpha)
        self.p1 += alpha * (change.astype(np.float32) - self.p1)
        self.transition += alpha * (transition_sample.astype(np.float32) - self.transition)
        self.clip += alpha * (clipped.astype(np.float32) - self.clip)
        self.previous_change = change.copy()
        self.updates += 1

        recomputed = self.updates % self.args.shadow_update_every == 0
        just_latched = False
        if recomputed:
            self.mask = self._derive_mask()
            comparison = self._compare()
            self.bad_streak = self.bad_streak + 1 if comparison.bad else 0
            if (
                not self.latched
                and self.args.shadow_stop_on_drift
                and self.bad_streak >= self.args.shadow_fail_consecutive
            ):
                self.latched = True
                just_latched = True
                self.last_details = comparison.details
            comparison = self._compare()
            comparison.bad_streak = self.bad_streak
            comparison.latched = self.latched
            self.comparison = comparison
        return self.comparison, just_latched
