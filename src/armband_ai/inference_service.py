"""Background loop: features → quality → model estimate → SQLite.

Prediction priority:
  1. Hailo HEF (if configured and ready)
  2. CPU multi-feature OLS
  3. CPU linear baseline
  4. Quality-only (no glucose estimate)
"""

from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .calibration import BaselineModel
from .config import ROOT, load_config
from .db import init_db, insert_inference
from .features import features_from_db
from .models import MultiFeatureModel
from .quality import score_window

log = logging.getLogger("armband_ai.inference")

# Keep in sync with features.WindowFeatures.to_vector() and train_mlp_onnx.py
FEATURE_KEYS = [
    "filt940_mean",
    "filt940_std",
    "filt940_min",
    "filt940_max",
    "filt940_slope",
    "raw940_mean",
    "bpm_mean",
    "bpm_std",
    "spo2_mean",
    "temp_mean",
    "motion_mean",
    "motion_max",
    "still_fraction",
    "moving_transitions",
    "batt_mean",
    "n_samples",
    "duration_s",
]


def resolve_db_path(cfg: dict) -> str:
    path = cfg["database"]["path"]
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return str(p)


def resolve_model_path(cfg: dict) -> Path:
    inf = cfg.get("inference") or {}
    raw = inf.get("model_path") or "models/baseline.json"
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


def resolve_multifeature_path(cfg: dict) -> Path:
    inf = cfg.get("inference") or {}
    raw = inf.get("multifeature_path") or "models/multifeature.json"
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


def resolve_hef_path(cfg: dict) -> Optional[Path]:
    hailo = cfg.get("hailo") or {}
    raw = (hailo.get("hef_path") or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


def resolve_norm_path(cfg: dict) -> Optional[Path]:
    hailo = cfg.get("hailo") or {}
    raw = (hailo.get("norm_path") or "").strip()
    if not raw:
        # conventional sibling of common HEF name
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


def _load_norm(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Failed to load norm JSON %s", path)
        return None


def _apply_norm(vec: np.ndarray, norm: Optional[dict[str, Any]]) -> np.ndarray:
    if not norm:
        return vec.astype(np.float32, copy=False)
    mean = np.asarray(norm.get("mean", []), dtype=np.float32)
    std = np.asarray(norm.get("std", []), dtype=np.float32)
    if mean.shape != vec.shape or std.shape != vec.shape:
        log.warning(
            "Norm shape mismatch (vec=%s mean=%s) — skipping normalization",
            vec.shape,
            mean.shape,
        )
        return vec.astype(np.float32, copy=False)
    std = np.where(std < 1e-6, 1.0, std)
    return ((vec - mean) / std).astype(np.float32)


def _predict_hailo(
    feats,
    hef_path: Optional[Path],
    norm_path: Optional[Path],
    runner_cache: dict,
) -> tuple[Optional[float], Optional[float], Optional[str], str]:
    """Try Hailo HEF. Returns estimate, r2 (None), path, source or signals miss."""
    if hef_path is None or not hef_path.exists():
        return None, None, None, ""

    try:
        from .hailo import HailoRunner
    except Exception:
        log.exception("Hailo module import failed")
        return None, None, None, ""

    cache_key = str(hef_path.resolve())
    runner = runner_cache.get(cache_key)
    if runner is None:
        runner = HailoRunner(hef_path=hef_path)
        runner_cache[cache_key] = runner

    if not runner.ready:
        log.debug("HailoRunner not ready: %s", runner.status())
        return None, None, None, ""

    try:
        vec = feats.to_vector(FEATURE_KEYS)
        norm = _load_norm(norm_path)
        # Also try sibling norm next to HEF if config omitted
        if norm is None:
            sibling = hef_path.with_name(hef_path.stem + "_norm.json")
            if not sibling.exists():
                sibling = hef_path.with_name("glucose_mlp_norm.json")
            norm = _load_norm(sibling if sibling.exists() else None)
        vec_n = _apply_norm(vec, norm)
        out = runner.infer(vec_n)
        est = float(np.asarray(out).reshape(-1)[0])
        return est, None, str(hef_path), "hailo"
    except Exception:
        log.exception("Hailo inference failed — falling back to CPU")
        return None, None, None, ""


def _predict_cpu(
    feats, model_path: Path, multi_path: Path
) -> tuple[Optional[float], Optional[float], Optional[str], str]:
    """Prefer multi-feature model, else linear baseline."""
    if multi_path.exists():
        try:
            m = MultiFeatureModel.load(multi_path)
            est = m.predict_row(feats.to_dict())
            return float(est), float(m.r2), str(multi_path), "cpu_multifeature"
        except Exception:
            log.exception("Multi-feature model failed")

    if model_path.exists():
        try:
            m = BaselineModel.load(model_path)
            est = float(m.predict(feats.filt940_mean))
            return est, float(m.r2), str(model_path), "cpu_baseline"
        except Exception:
            log.exception("Baseline model failed")

    return None, None, None, "cpu_quality"


def _predict(
    feats,
    model_path: Path,
    multi_path: Path,
    hef_path: Optional[Path] = None,
    norm_path: Optional[Path] = None,
    runner_cache: Optional[dict] = None,
) -> tuple[Optional[float], Optional[float], Optional[str], str]:
    cache = runner_cache if runner_cache is not None else {}
    est, r2, path, source = _predict_hailo(feats, hef_path, norm_path, cache)
    if source == "hailo":
        return est, r2, path, source
    return _predict_cpu(feats, model_path, multi_path)


def run_once(
    db_path: str,
    window_minutes: float = 5.0,
    model_path: Optional[Path] = None,
    multifeature_path: Optional[Path] = None,
    hef_path: Optional[Path] = None,
    norm_path: Optional[Path] = None,
    runner_cache: Optional[dict] = None,
) -> Optional[dict]:
    init_db(db_path)
    feats = features_from_db(db_path, minutes=int(max(1, round(window_minutes))))
    if feats is None:
        log.info("No PPG data in last %.1f min – skip", window_minutes)
        return None

    quality = score_window(feats)
    mp = model_path or (ROOT / "models" / "baseline.json")
    mfp = multifeature_path or (ROOT / "models" / "multifeature.json")
    glucose_est, baseline_r2, model_used, source = _predict(
        feats,
        mp,
        mfp,
        hef_path=hef_path,
        norm_path=norm_path,
        runner_cache=runner_cache,
    )

    row_id = insert_inference(
        db_path,
        window_minutes=window_minutes,
        n_samples=feats.n_samples,
        quality_score=quality.score,
        quality_label=quality.label,
        quality_reasons=quality.reasons,
        filt940_mean=feats.filt940_mean,
        still_fraction=feats.still_fraction,
        bpm_mean=feats.bpm_mean,
        glucose_estimate=glucose_est,
        baseline_r2=baseline_r2,
        model_path=model_used,
        feature_json=feats.to_dict(),
        source=source,
    )

    result = {
        "id": row_id,
        "quality_score": quality.score,
        "quality_label": quality.label,
        "glucose_estimate": glucose_est,
        "n_samples": feats.n_samples,
        "source": source,
    }
    log.info(
        "inference id=%s quality=%.0f (%s) glucose=%s src=%s",
        row_id,
        quality.score,
        quality.label,
        f"{glucose_est:.0f}" if glucose_est is not None else "—",
        source,
    )
    return result


def run_loop(config: dict | None = None) -> None:
    cfg = config or load_config()
    db_path = resolve_db_path(cfg)
    inf = cfg.get("inference") or {}
    interval = float(inf.get("interval_seconds", 30))
    window_minutes = float(inf.get("window_minutes", 5))
    model_path = resolve_model_path(cfg)
    multi_path = resolve_multifeature_path(cfg)
    hef_path = resolve_hef_path(cfg)
    norm_path = resolve_norm_path(cfg)
    runner_cache: dict = {}

    init_db(db_path)
    log.info(
        "Inference service: interval=%.0fs window=%.1fmin db=%s "
        "baseline=%s multi=%s hef=%s",
        interval,
        window_minutes,
        db_path,
        model_path,
        multi_path,
        hef_path or "(none)",
    )

    running = True

    def _stop(signum, frame):
        nonlocal running
        log.info("Shutdown signal %s", signum)
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        try:
            run_once(
                db_path,
                window_minutes=window_minutes,
                model_path=model_path,
                multifeature_path=multi_path,
                hef_path=hef_path,
                norm_path=norm_path,
                runner_cache=runner_cache,
            )
        except Exception:
            log.exception("Inference iteration failed")
        slept = 0.0
        while running and slept < interval:
            time.sleep(min(1.0, interval - slept))
            slept += 1.0

    log.info("Inference service stopped")
