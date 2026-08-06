#!/usr/bin/env python3
"""Train a small MLP on quality-gated calibration pairs and export ONNX + norm JSON.

Feature order matches WindowFeatures.to_vector() default keys (17 floats).

Usage:
  python scripts/train_mlp_onnx.py --from-db --min-quality 60 --min-still 0.7
  python scripts/train_mlp_onnx.py --pairs exports/pairs.csv --epochs 400
  python scripts/train_mlp_onnx.py --from-db --out-onnx models/glucose_mlp.onnx

Requires: torch, onnx (and project deps for --from-db).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Stable contract — must match features.WindowFeatures.to_vector()
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


def _pairs_from_db(min_quality: float, min_still: float, window: int):
    from armband_ai.calibration import build_calibration_pairs
    from armband_ai.config import load_config, ROOT as PROJECT_ROOT
    from armband_ai.features import extract_window_features
    from armband_ai.db import get_connection, init_db
    import pandas as pd
    from datetime import timedelta

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    # Base pairs (reduced aggregates) for gating + glucose labels
    base = build_calibration_pairs(
        db_path,
        window_seconds=window,
        prefer_still=True,
        min_quality=min_quality,
        min_still_fraction=min_still,
    )
    if base.empty:
        return base

    # Enrich with full WindowFeatures over the same preferred window
    init_db(db_path)
    with get_connection(db_path) as conn:
        ppg = pd.read_sql_query(
            "SELECT * FROM ppg_readings ORDER BY received_at ASC",
            conn,
        )
    if ppg.empty:
        return pd.DataFrame()

    ppg["received_at"] = pd.to_datetime(ppg["received_at"], utc=True)
    half = timedelta(seconds=window)
    rows = []
    for _, row in base.iterrows():
        t = row["recorded_at"]
        if not hasattr(t, "tzinfo") or t.tzinfo is None:
            t = pd.to_datetime(t, utc=True)
        mask = (ppg["received_at"] >= t - half) & (ppg["received_at"] <= t + half)
        candidates = ppg.loc[mask].copy()
        if candidates.empty:
            continue
        if (candidates.get("moving", 0) == 0).any():
            still = candidates[candidates["moving"] == 0]
            if len(still) >= 4:
                candidates = still
        feats = extract_window_features(candidates)
        if feats is None:
            continue
        d = feats.to_dict()
        d["glucose_mgdl"] = float(row["glucose_mgdl"])
        d["quality_score"] = float(row.get("quality_score", 0))
        rows.append(d)
    return pd.DataFrame(rows)


def _load_pairs_csv(path: Path) -> "pd.DataFrame":
    import pandas as pd

    df = pd.read_csv(path)
    missing = [k for k in FEATURE_KEYS if k not in df.columns]
    if missing:
        # Allow reduced pair CSVs: fill missing with 0 and warn
        print(f"WARNING: missing columns {missing} — filling with 0.0")
        for k in missing:
            df[k] = 0.0
    if "glucose_mgdl" not in df.columns:
        raise SystemExit("pairs CSV must include glucose_mgdl")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Train tiny MLP → ONNX for Hailo path")
    parser.add_argument("--from-db", action="store_true", help="Build pairs from SQLite")
    parser.add_argument("--pairs", type=str, default=None, help="CSV of pairs / features")
    parser.add_argument("--min-quality", type=float, default=60.0)
    parser.add_argument("--min-still", type=float, default=0.7)
    parser.add_argument("--window", type=int, default=180)
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--hidden", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument(
        "--out-onnx",
        type=str,
        default=str(ROOT / "models" / "glucose_mlp.onnx"),
    )
    parser.add_argument(
        "--out-norm",
        type=str,
        default=str(ROOT / "models" / "glucose_mlp_norm.json"),
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("Install PyTorch first: pip install torch")
        sys.exit(1)

    if args.from_db:
        pairs = _pairs_from_db(args.min_quality, args.min_still, args.window)
    elif args.pairs:
        pairs = _load_pairs_csv(Path(args.pairs))
    else:
        print("Provide --from-db or --pairs path")
        sys.exit(1)

    if pairs is None or len(pairs) < 8:
        print(f"Need ≥ 8 pairs for a tiny MLP (got {0 if pairs is None else len(pairs)}).")
        sys.exit(1)

    X = pairs.reindex(columns=FEATURE_KEYS).fillna(0.0).to_numpy(dtype=np.float64)
    y = pairs["glucose_mgdl"].to_numpy(dtype=np.float64)

    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    Xn = (X - mean) / std

    torch.manual_seed(args.seed)
    n_in = len(FEATURE_KEYS)

    class MLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(n_in, args.hidden),
                nn.ReLU(),
                nn.Linear(args.hidden, args.hidden // 2),
                nn.ReLU(),
                nn.Linear(args.hidden // 2, 1),
            )

        def forward(self, x):
            return self.net(x).squeeze(-1)

    model = MLP()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()

    xt = torch.tensor(Xn, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32)

    model.train()
    for epoch in range(1, args.epochs + 1):
        opt.zero_grad()
        pred = model(xt)
        loss = loss_fn(pred, yt)
        loss.backward()
        opt.step()
        if epoch % 100 == 0 or epoch == 1:
            mae = (pred.detach() - yt).abs().mean().item()
            print(f"epoch {epoch:4d}  mse={loss.item():.2f}  mae={mae:.2f}")

    model.eval()
    with torch.no_grad():
        pred = model(xt).numpy()
    mae = float(np.mean(np.abs(pred - y)))
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    print(f"\nFinal train MAE={mae:.2f}  RMSE={rmse:.2f}  n={len(y)}")
    print("(In-sample metrics only — hold out pairs when you have enough data.)")

    # ONNX export: input [batch, 17] normalized features
    out_onnx = Path(args.out_onnx)
    out_onnx.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, n_in, dtype=torch.float32)
    try:
        torch.onnx.export(
            model,
            dummy,
            str(out_onnx),
            input_names=["features"],
            output_names=["glucose_mgdl"],
            dynamic_axes={"features": {0: "batch"}, "glucose_mgdl": {0: "batch"}},
            opset_version=17,
        )
    except TypeError:
        # older torch
        torch.onnx.export(
            model,
            dummy,
            str(out_onnx),
            input_names=["features"],
            output_names=["glucose_mgdl"],
            opset_version=13,
        )
    print(f"ONNX → {out_onnx}")

    norm = {
        "feature_keys": FEATURE_KEYS,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "n_pairs": int(len(y)),
        "train_mae": mae,
        "train_rmse": rmse,
        "hidden": args.hidden,
        "note": "Apply z-score with these mean/std before HailoRunner.infer()",
    }
    out_norm = Path(args.out_norm)
    out_norm.parent.mkdir(parents=True, exist_ok=True)
    out_norm.write_text(json.dumps(norm, indent=2), encoding="utf-8")
    print(f"Norm  → {out_norm}")
    print("\nNext: compile ONNX → HEF on x86_64 with Hailo DFC (hw_arch=hailo8).")
    print("See docs/HAILO_MODEL.md")


if __name__ == "__main__":
    main()
