#!/usr/bin/env python3

"""Train a small MLP on quality-gated calibration pairs and export ONNX + norm JSON.

*** DISABLED AT PILOT SCALE (2026-08-13, ASK 10) ***

Default architecture (17 → --hidden → max(2,hidden//2) → 1) has
  (17*h + h) + (h*(h//2) + h//2) + ((h//2)*1 + 1) free parameters.
At the default --hidden=32 that is exactly 1121 parameters.

One pair per parameter is already the n = p condition (perfect in-sample
fit on noise). A defensible multiple puts the requirement in the thousands
of pairs *per subject*. The two-person pilot will not reach four-figure
per-subject pair counts this year.

Correct state for this path is DISABLED, not gated. The script exits with
an explicit reason and does not train. Code remains in tree for the day
data volume justifies it. When that day comes, any floor must be DERIVED
from the actual layer widths at train time (--hidden is a CLI argument);
MIN_PAIRS_PER_SUBJECT must not exist as a hardcoded constant in this file.

Feature order matches WindowFeatures.to_vector() default keys (17 floats).

Usage (will refuse until the disable is lifted):
    python scripts/train_mlp_onnx.py --from-db --min-quality 60 ...

Requires: torch, onnx (and project deps for --from-db).

Exit codes: 0 ok · 1 usage/data/disabled · 2 dependency/DB/IO failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# Hard disable — do not remove the early exit until per-subject pair counts
# are in four figures and a derived (not hardcoded) floor is in place.
# ---------------------------------------------------------------------------
MLP_PATH_DISABLED = True
MLP_DISABLE_REASON = (
    "MLP/Hailo train path DISABLED at pilot scale (2026-08-13 ASK 10). "
    "Default 17→32→16→1 has 1121 free parameters; defensible training needs "
    "thousands of pairs PER SUBJECT (n ≫ p). Two-person pilot will not reach "
    "that this year. Path stays in tree for future data volume; do not train "
    "or deploy until then. When re-enabled, any floor must be DERIVED from "
    "actual layer widths at train time — no hardcoded MIN_PAIRS in this file."
)

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


def _pairs_from_db(
    min_quality: float,
    min_still: float,
    window: int,
    min_clean_streak: int = 0,
    prefer_still: bool = True,
):
    from datetime import timedelta

    import pandas as pd

    from armband_ai.calibration import build_calibration_pairs
    from armband_ai.config import ROOT as PROJECT_ROOT
    from armband_ai.config import load_config
    from armband_ai.db import DatabaseError, get_connection, init_db
    from armband_ai.features import extract_window_features

    cfg = load_config()
    db_path = cfg["database"]["path"]
    if not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)

    try:
        base = build_calibration_pairs(
            db_path,
            window_seconds=window,
            prefer_still=prefer_still,
            min_quality=min_quality,
            min_still_fraction=min_still,
            min_clean_streak=min_clean_streak,
        )
    except DatabaseError:
        raise
    except Exception as e:
        raise RuntimeError(f"build_calibration_pairs failed: {e}") from e

    if base.empty:
        return base

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
        # Mirror build_calibration_pairs' prefer_still rule exactly (any still
        # sample => filter to still-only) so the feature vector we train on
        # matches the candidate set that earned this pair its quality_score.
        if prefer_still and (candidates.get("moving", 0) == 0).any():
            candidates = candidates[candidates["moving"] == 0]
        feats = extract_window_features(candidates)
        if feats is None:
            continue
        d = feats.to_dict()
        d["glucose_mgdl"] = float(row["glucose_mgdl"])
        d["quality_score"] = float(row.get("quality_score", 0))
        rows.append(d)
    return pd.DataFrame(rows)


def _load_pairs_csv(path: Path):
    import pandas as pd

    if not path.exists():
        raise FileNotFoundError(f"pairs CSV not found: {path}")
    try:
        df = pd.read_csv(path)
    except Exception as e:
        raise RuntimeError(f"failed to read CSV {path}: {e}") from e

    if df.empty:
        raise ValueError(f"pairs CSV is empty: {path}")

    missing = [k for k in FEATURE_KEYS if k not in df.columns]
    if missing:
        print(
            f"WARNING: missing columns {missing} — filling with 0.0",
            file=sys.stderr,
        )
        for k in missing:
            df[k] = 0.0
    if "glucose_mgdl" not in df.columns:
        raise ValueError("pairs CSV must include glucose_mgdl column")
    return df


def _param_count(n_in: int, hidden: int) -> int:
    """Free parameters for the default Sequential used below."""
    h2 = max(2, hidden // 2)
    return (n_in * hidden + hidden) + (hidden * h2 + h2) + (h2 * 1 + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train tiny MLP → ONNX for Hailo path")
    parser.add_argument("--from-db", action="store_true", help="Build pairs from SQLite")
    parser.add_argument("--pairs", type=str, default=None, help="CSV of pairs / features")
    parser.add_argument("--min-quality", type=float, default=60.0)
    parser.add_argument("--min-still", type=float, default=0.7)
    parser.add_argument(
        "--min-clean-streak",
        type=int,
        default=10,
        help="Min consecutive still+optically-stable samples (0=off, recommend 10-15)",
    )
    parser.add_argument("--no-prefer-still", action="store_true")
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

    # ---- Hard disable (ASK 10) ---------------------------------------------
    if MLP_PATH_DISABLED:
        n_in = len(FEATURE_KEYS)
        p = _param_count(n_in, args.hidden)
        print(
            f"ERROR: {MLP_DISABLE_REASON}\n"
            f"  (this invocation: n_in={n_in} hidden={args.hidden} → {p} free parameters)",
            file=sys.stderr,
        )
        return 1

    if args.epochs < 1 or args.hidden < 2:
        print("ERROR: --epochs >= 1 and --hidden >= 2 required", file=sys.stderr)
        return 1
    if args.min_clean_streak < 0:
        print("ERROR: --min-clean-streak must be >= 0", file=sys.stderr)
        return 1
    if not args.from_db and not args.pairs:
        print("ERROR: provide --from-db or --pairs path", file=sys.stderr)
        return 1

    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("ERROR: Install PyTorch first: pip install torch", file=sys.stderr)
        return 2

    try:
        if args.from_db:
            pairs = _pairs_from_db(
                args.min_quality,
                args.min_still,
                args.window,
                min_clean_streak=args.min_clean_streak,
                prefer_still=not args.no_prefer_still,
            )
        else:
            pairs = _load_pairs_csv(Path(args.pairs))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        from armband_ai.db import DatabaseError

        if isinstance(e, DatabaseError):
            print(f"ERROR: database failure: {e}", file=sys.stderr)
        else:
            print(f"ERROR: failed to load pairs: {e}", file=sys.stderr)
        return 2

    # If the disable is ever lifted, the floor must be DERIVED from the
    # actual architecture, not imported as a constant from models.py.
    # Example (not active while DISABLED):
    #   p = _param_count(len(FEATURE_KEYS), args.hidden)
    #   min_pairs = max(p * 5, 100)   # illustrative multiple; choose deliberately
    n_in = len(FEATURE_KEYS)
    p = _param_count(n_in, args.hidden)
    min_pairs = p * 5  # placeholder only — path is disabled above

    if pairs is None or len(pairs) < min_pairs:
        n = 0 if pairs is None else len(pairs)
        print(
            f"ERROR: need >= {min_pairs} quality-gated pairs for this architecture "
            f"(p={p} free params × 5; got {n}). Per-subject partition still required.",
            file=sys.stderr,
        )
        return 1

    try:
        X = pairs.reindex(columns=FEATURE_KEYS).fillna(0.0).to_numpy(dtype=np.float64)
        y = pairs["glucose_mgdl"].to_numpy(dtype=np.float64)
        if not np.isfinite(X).all() or not np.isfinite(y).all():
            print("ERROR: non-finite values in features or glucose labels", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: failed to build feature matrix: {e}", file=sys.stderr)
        return 1

    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    Xn = (X - mean) / std

    torch.manual_seed(args.seed)

    class MLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(n_in, args.hidden),
                nn.ReLU(),
                nn.Linear(args.hidden, max(2, args.hidden // 2)),
                nn.ReLU(),
                nn.Linear(max(2, args.hidden // 2), 1),
            )

        def forward(self, x):
            return self.net(x).squeeze(-1)

    try:
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
            if not torch.isfinite(loss):
                print(f"ERROR: non-finite loss at epoch {epoch}", file=sys.stderr)
                return 2
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
    except Exception as e:
        print(f"ERROR: training failed: {e}", file=sys.stderr)
        return 2

    out_onnx = Path(args.out_onnx)
    out_norm = Path(args.out_norm)
    try:
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
            torch.onnx.export(
                model,
                dummy,
                str(out_onnx),
                input_names=["features"],
                output_names=["glucose_mgdl"],
                opset_version=13,
            )
        print(f"ONNX → {out_onnx}")
    except Exception as e:
        print(f"ERROR: ONNX export failed: {e}", file=sys.stderr)
        return 2

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
    try:
        out_norm.parent.mkdir(parents=True, exist_ok=True)
        out_norm.write_text(json.dumps(norm, indent=2), encoding="utf-8")
        print(f"Norm  → {out_norm}")
    except OSError as e:
        print(f"ERROR: cannot write norm JSON {out_norm}: {e}", file=sys.stderr)
        return 2

    print("\nNext: compile ONNX → HEF on x86_64 with Hailo DFC (hw_arch=hailo8).")
    print("See docs/HAILO_MODEL.md")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
