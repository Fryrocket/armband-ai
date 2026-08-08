# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

> **Part of [BGM](https://github.com/Fryrocket/BGM)** – the umbrella wearable blood-glucose monitoring project.  
> Wearable firmware companion: **[armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm)**.

**v0.4.5** – Drift monitor (still-only filt940 median vs last-cal baseline) + insert-time soft validation for BPM/temp; quality score on raw window, consecutive-clean streak gates, tighter optical penalties, Hailo path, MLP→ONNX trainer, multi-feature OLS.

| Doc | Purpose |
|-----|---------|
| **[HARDWARE.md](HARDWARE.md)** | BOM: Pi, AI HAT, armband, boot SSD |
| **[docs/HAILO_DRIVER.md](docs/HAILO_DRIVER.md)** | Driver / firmware / HailoRT install & diagnose |
| **[docs/HAILO_MODEL.md](docs/HAILO_MODEL.md)** | Train MLP → ONNX → DFC HEF → deploy on Pi |
| **[docs/PIPELINE.md](docs/PIPELINE.md)** | MQTT → DB → features → quality → models → Hailo |
| **[docs/LIBRE_FLOW.md](docs/LIBRE_FLOW.md)** | How to log Libre/fingerstick references |
| **[docs/GIT_AUTO_PULL.md](docs/GIT_AUTO_PULL.md)** | Auto-pull exit codes & error-handling examples |
| **[docs/LOG_ROTATION.md](docs/LOG_ROTATION.md)** | Log rotation, zstd/gzip, fallbacks |

## SpO₂ convention

PPG `spo2` is an integer percent. **Values < 0 (usually `-1`) mean invalid / not computed** and are ignored in feature averages. Schema and firmware may still carry the field for when SpO₂ is re-enabled on the armband.

## Hailo-8 driver (short path)

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y dkms hailo-all zstd
sudo reboot

hailortcli fw-control identify
python scripts/hailo_diagnose.py
python scripts/hailo_identify.py --extended --save models/hailo_device.json
```

- **`hailo-all`** = AI HAT+ / AI Kit (Hailo-8 / 8L)
- **`hailo-h10-all`** = AI HAT+ 2 only — do not mix
- PCIe Gen3 via `raspi-config` if needed

Silicon (photos): industrial **Hailo-8 / HNC18BI11BH (26 TOPS)** — confirm with `Device Architecture`.

### Hailo model path (optional)

CPU baseline / multi-feature work without an HEF. To run a neural net on the NPU:

1. Collect quality-gated Libre pairs
2. `python scripts/train_mlp_onnx.py --from-db --min-quality 60` → ONNX + norm JSON
3. Compile ONNX → HEF on **x86_64** with Hailo DFC (`hw_arch=hailo8`)
4. Set `hailo.hef_path` (and optional `norm_path`) in `config.yaml`

Full checklist: **[docs/HAILO_MODEL.md](docs/HAILO_MODEL.md)**.

Inference priority: **Hailo HEF → CPU multifeature → CPU baseline → quality-only**.

## What runs on the Pi

| Service | Command |
|---------|---------|
| MQTT logger | `python scripts/run_logger.py` |
| Inference loop | `python scripts/run_inference.py` |
| Dashboard | `bash scripts/run_dashboard.sh` |

```bash
git pull --rebase && source .venv/bin/activate
cp -n config.example.yaml config.yaml
python scripts/run_logger.py &
python scripts/run_inference.py &
bash scripts/run_dashboard.sh
```

### Git auto-pull (hooks + optional timer)

```bash
cd ~/armband-ai
bash scripts/install_git_hooks.sh
bash scripts/install_git_hooks.sh --timer   # optional hourly
bash scripts/git_auto_pull.sh; echo exit=$?
```

Exit codes: **0** ok/skip · **1** local · **2** network · **3** rebase conflict.  
See **[docs/GIT_AUTO_PULL.md](docs/GIT_AUTO_PULL.md)**.

### Logs

Default archive format is **zstd** (`logging.compression: zstd`). If `zstd` is missing, rotation still works and keeps a plain `.1` file — see **[docs/LOG_ROTATION.md](docs/LOG_ROTATION.md)**.

```bash
sudo apt install -y zstd
```

## Libre + calibration

See **[docs/LIBRE_FLOW.md](docs/LIBRE_FLOW.md)**.

**Quick tip:** Sit still 1–2 minutes with the armband streaming before logging a Libre/fingerstick reading. High still-fraction + sustained clean streak produce the pairs that actually improve the model.

```bash
python scripts/log_glucose.py 142 --notes "still"
python scripts/calibrate.py --min-quality 60 --min-still 0.7 --min-clean-streak 12
python scripts/train_multifeature.py --min-quality 60 --min-clean-streak 12
# optional neural path:
python scripts/train_mlp_onnx.py --from-db --min-quality 60
```

### Quality gates (calibration)

Calibration pairs are quality-gated before they enter a model. The default path already prefers still samples and applies a 0–100 heuristic score.

**Fixed (2026-08):** Three related gate-order bugs in `build_calibration_pairs()`:

1. **`still_fraction`** used to be computed *after* the prefer-still filter → trivially ~1.0. Now computed on the **raw window first**.
2. **`quality_score`** used to be scored *after* the same filter (via `score_dataframe` on the cleaned subset). Because the quality heuristic is dominated by motion terms, this silently inflated scores. Quality is now computed from the same raw `WindowFeatures` via `score_window()` **before** prefer-still is applied.
3. **Consecutive-clean streak** (`max_clean_streak` / `clean_fraction`) is also evaluated on the raw window and gated before any filtering. Prefer-still alone can no longer cherry-pick short clean snippets.

Prefer-still still controls which rows are averaged into `filt940_mean` etc.; it no longer affects what is scored or gated. See `src/armband_ai/calibration.py`.

**Recommended tighter gates** (especially under real contact noise and multi-day use):

| Gate | Purpose | Suggested default |
|------|---------|-------------------|
| `min_quality` | Overall heuristic score | ≥ 60–65 |
| `min_still_fraction` | Fraction of non-moving samples | ≥ 0.70 |
| **`min_clean_streak`** | Sustained still *and* optically stable samples | ≥ 10–15 samples |
| Optical CV / range / slope | Reject intermittent contact loss that still passes a simple `moving==0` check | CV ≲ 0.045, relative range ≲ 0.12, \|slope\| ≲ 2.5 |

Prefer-still pairing alone can cherry-pick short clean snippets inside a noisy window. A **consecutive-clean** requirement forces a real sustained stable period before a Libre reading is accepted as a calibration pair.

### Drift monitoring

Within-window quality cannot see slow baseline shift (contact change, temperature, sensor aging). Track a **still-only rolling median** of `filt940` (e.g. every 1–2 hours) and compare it to the median at the last successful calibration:

| \|Δ median\| vs last cal | Action |
|--------------------------|--------|
| ≳ 40 | Normal / mild warn |
| ≳ 80 | Alert — consider re-calibration |
| sustained large shift | Mark model stale; collect new still Libre pairs |

Implemented in `src/armband_ai/drift_monitor.py`. Successful `calibrate.py` (and train scripts) snapshot the still-only median to `models/drift_baseline.json`. Use `compute_drift_from_db()` or the DriftMonitor class to surface delta + `is_stale` on the dashboard. Advisory only — does not block inference.

## Hardening recommendations

Practical improvements ranked by leverage. Most are incremental; the core pipeline already runs without them.

### High priority (data quality & model health)

1. **Consecutive-clean streak in quality / calibration** — **done 2026-08-08**  
   `max_clean_streak` + `clean_fraction` on `WindowFeatures`; calibration gate `min_clean_streak`; quality penalties for short streaks. Enable with `--min-clean-streak 12` or `calibration.min_clean_streak` in config.

2. **Quality score on raw window** — **done 2026-08-08**  
   `score_window(raw_feats)` now runs before prefer-still filtering, so `min_quality` reflects the true window, not a motion-scrubbed subset.

3. **Drift monitor (still-only median)** — **done 2026-08-08**  
   Still-only rolling median of `filt940` vs snapshot at last successful calibration (`models/drift_baseline.json`). Advisory `is_stale` when |Δ| ≥ threshold (default 40). See `src/armband_ai/drift_monitor.py`.

4. **Tighter optical checks in `quality.py`** — **partially applied**  
   Milder CV band (~0.045) and slope band (~2.5) now penalize. Further tuning still useful under heavy contact noise.

5. **Light insert-time validation in `db.py`** — **done 2026-08-08**  
   Soft-check BPM (35–220) and temp (30–45 °C) on `insert_reading`. Log a warning and clamp extremes rather than failing the insert. SpO₂ < 0 already handled correctly.

### Medium priority (ops & UI)

6. **Schema versioning**  
   Add a `schema_version` table (or PRAGMA user_version) and small migration helpers before adding columns (e.g. lag-corrected estimates, Hailo scores, drift snapshots). `init_db` alone is fine while the schema is stable.

7. **Dashboard delete confirmation**  
   Calibration tab: require an explicit confirm step before `delete_libre` so a fat-fingered id does not drop a good reference.

8. **Cache `count_readings`**  
   Live tab currently counts on every refresh. Cache for 30–60 s (or only recompute every N auto-refreshes). Negligible today; avoids future cost as the table grows.

9. **Log-compression fallback visibility**  
   Rotation already falls back to plain `.1` when `zstd` is missing. Surface a one-line status on the dashboard (or a startup warning metric) so the fallback is not silent forever.

### Lower priority (structure & polish)

10. **Known-issues section in `docs/HAILO_DRIVER.md`**  
    Firmware / DKMS version mismatches, Gen2 vs Gen3 link, venv + system-site-packages notes. Link to Pi / Hailo issues when they appear.

11. **Full `WindowFeatures` at Libre timestamps for multi-feature training**  
    `build_calibration_pairs` currently aggregates a reduced column set (plus streak fields). Computing the full feature vector per pair unlocks a stronger multi-feature model once you have enough high-quality still readings. (`train_mlp_onnx.py --from-db` already rebuilds full features.)

12. **Hailo path in the inference loop** — **done in v0.4.2**  
    When `hailo.hef_path` is set and the runner is ready, prefer HEF output and fall back to CPU multi-feature / baseline. See [docs/HAILO_MODEL.md](docs/HAILO_MODEL.md).

13. **Retention / vacuum**  
    On a 250 GB SSD you have headroom, but a simple retention policy (or periodic export + `VACUUM`) keeps the DB tidy after months of continuous logging.

### Testing notes (simulation findings)

Adversarial week-long simulations (contact-loss episodes, bad-still optical noise, temperature swings, slow baseline drift) showed:

- Prefer-still + **original** quality gates (before the still_fraction / quality_score order fixes) kept nearly 100% of pairs but let drift and residual optical noise into the model (R² collapse over days). Both gate-order bugs are fixed in `calibration.py`; `min_still_fraction` and `min_quality` now measure the raw window.
- Prefer-still can still cherry-pick short still snippets inside a noisy window — **consecutive-clean** + tighter optical checks reject those while preserving a usable pair set.
- Drift monitoring (still-only median vs early baseline) correctly flagged multi-day baseline shift that no within-window score can see.

Root-cause gate order (still + quality) + consecutive-clean + drift monitoring together close the main gaps between “clean desk data” and real wearable use.

## systemd

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

## License

**GNU GPLv3 or later** — see [LICENSE](LICENSE).

⚠️ Experimental only. Not a medical device.
