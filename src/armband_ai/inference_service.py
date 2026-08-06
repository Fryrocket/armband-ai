"""Background loop: features → quality → baseline estimate → SQLite."""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Optional

from .calibration import BaselineModel
from .config import ROOT, load_config
from .db import init_db, insert_inference
from .features import features_from_db
from .quality import score_window

log = logging.getLogger("armband_ai.inference")


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


def run_once(
    db_path: str,
    window_minutes: float = 5.0,
    model_path: Optional[Path] = None,
) -> Optional[dict]:
    """Compute one quality + optional glucose snapshot and store it."""
    init_db(db_path)
    feats = features_from_db(db_path, minutes=int(max(1, round(window_minutes))))
    if feats is None:
        log.info("No PPG data in last %.1f min – skip", window_minutes)
        return None

    quality = score_window(feats)
    glucose_est = None
    baseline_r2 = None
    model_used = None

    mp = model_path or (ROOT / "models" / "baseline.json")
    if mp.exists():
        try:
            model = BaselineModel.load(mp)
            glucose_est = float(model.predict(feats.filt940_mean))
            baseline_r2 = float(model.r2)
            model_used = str(mp)
        except Exception:
            log.exception("Failed to load/apply baseline model %s", mp)

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
        source="cpu_quality",
    )

    result = {
        "id": row_id,
        "quality_score": quality.score,
        "quality_label": quality.label,
        "glucose_estimate": glucose_est,
        "n_samples": feats.n_samples,
        "filt940_mean": feats.filt940_mean,
        "still_fraction": feats.still_fraction,
    }
    log.info(
        "inference id=%s quality=%.0f (%s) glucose=%s n=%s",
        row_id,
        quality.score,
        quality.label,
        f"{glucose_est:.0f}" if glucose_est is not None else "—",
        feats.n_samples,
    )
    return result


def run_loop(config: dict | None = None) -> None:
    cfg = config or load_config()
    db_path = resolve_db_path(cfg)
    inf = cfg.get("inference") or {}
    interval = float(inf.get("interval_seconds", 30))
    window_minutes = float(inf.get("window_minutes", 5))
    model_path = resolve_model_path(cfg)

    init_db(db_path)
    log.info(
        "Inference service starting: interval=%.0fs window=%.1fmin db=%s model=%s",
        interval,
        window_minutes,
        db_path,
        model_path,
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
            run_once(db_path, window_minutes=window_minutes, model_path=model_path)
        except Exception:
            log.exception("Inference iteration failed")
        # Sleep in small chunks so SIGTERM is responsive
        slept = 0.0
        while running and slept < interval:
            time.sleep(min(1.0, interval - slept))
            slept += 1.0

    log.info("Inference service stopped")
