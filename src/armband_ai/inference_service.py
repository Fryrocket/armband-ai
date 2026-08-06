"""Background loop: features → quality → model estimate → SQLite."""

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
from .models import MultiFeatureModel
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


def resolve_multifeature_path(cfg: dict) -> Path:
    inf = cfg.get("inference") or {}
    raw = inf.get("multifeature_path") or "models/multifeature.json"
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


def _predict(feats, model_path: Path, multi_path: Path) -> tuple[Optional[float], Optional[float], Optional[str], str]:
    """Prefer multi-feature model, else linear baseline. Returns estimate, r2, path, source."""
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


def run_once(
    db_path: str,
    window_minutes: float = 5.0,
    model_path: Optional[Path] = None,
    multifeature_path: Optional[Path] = None,
) -> Optional[dict]:
    init_db(db_path)
    feats = features_from_db(db_path, minutes=int(max(1, round(window_minutes))))
    if feats is None:
        log.info("No PPG data in last %.1f min – skip", window_minutes)
        return None

    quality = score_window(feats)
    mp = model_path or (ROOT / "models" / "baseline.json")
    mfp = multifeature_path or (ROOT / "models" / "multifeature.json")
    glucose_est, baseline_r2, model_used, source = _predict(feats, mp, mfp)

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

    init_db(db_path)
    log.info(
        "Inference service: interval=%.0fs window=%.1fmin db=%s baseline=%s multi=%s",
        interval, window_minutes, db_path, model_path, multi_path,
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
            )
        except Exception:
            log.exception("Inference iteration failed")
        slept = 0.0
        while running and slept < interval:
            time.sleep(min(1.0, interval - slept))
            slept += 1.0

    log.info("Inference service stopped")
