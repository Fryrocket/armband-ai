# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

> **Part of [BGM](https://github.com/Fryrocket/BGM)** – the umbrella wearable blood-glucose monitoring project.  
> Wearable firmware companion: **[armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm)**.

**v0.4.2** – Hailo inference path (HEF → CPU fallback), MLP→ONNX trainer, quality-gate + drift docs, multi-feature OLS, HEF-ready `HailoRunner`.

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

**Quick tip:** Sit still 1–2 minutes with the armband streaming before logging a Libre/fingerstick reading. High still-fraction + stable optics produce the pairs that actually improve the model.

```bash
python scripts/log_glucose.py 142 --notes "still"
python scripts/calibrate.py --min-quality 60 --min-still 0.7
python scripts/train_multifeature.py --min-quality 60
# optional neural path:
python scripts/train_mlp_onnx.py --from-db --min-quality 60
```

### Quality gates (calibration)

Calibration pairs are quality-gated before they enter a model. The default path already prefers still samples and applies a 0–100 heuristic score.

**Recommended tighter gates** (especially under real contact noise and multi-day use):

| Gate | Purpose | Suggested default |
|------|---------|-------------------|
| `min_quality` | Overall heuristic score | ≥ 60–65 |
| `min_still_fraction` | Fraction of non-moving samples | ≥ 0.70 |
| **Consecutive clean streak** | Sustained still *and* optically stable samples (low rolling CV / range) | ≥ 10–15 samples |
| Optical CV / range / slope | Reject intermittent contact loss that still passes a simple `moving==0` check | CV ≲ 0.045, relative range ≲ 0.12, \|slope\| ≲ 2.5 |

Prefer-still pairing alone can cherry-pick short clean snippets inside a noisy window. A **consecutive-clean** requirement forces a real sustained stable period before a Libre reading is accepted as a calibration pair.

### Drift monitoring

Within-window quality cannot see slow baseline shift (contact change, temperature, sensor aging). Track a **still-only rolling median** of `filt940` (e.g. every 1–2 hours) and compare it to the median at the last successful calibration:

| \|Δ median\| vs last cal | Action |
|--------------------------|--------|
| ≳ 40 | Normal / mild warn |
| ≳ 80 | Alert — consider re-calibration |
| sustained large shift | Mark model stale; collect new still Libre pairs |

Surface the current delta on the dashboard AI / Calibration tabs. Re-run `calibrate.py` / `train_multifeature.py` after an alert once you have fresh high-quality pairs.

## Hardening recommendations

Practical improvements ranked by leverage. Most are incremental; the core pipeline already runs without them.

### High priority (data quality & model health)

1. **Consecutive-clean streak in quality / calibration**  
   Add `max_clean_streak` and `clean_fraction` to `WindowFeatures`. Require a minimum streak of samples that are both still *and* optically stable (rolling CV / range) before accepting a calibration pair. Prevents prefer-still from accepting intermittent contact-loss windows.

2. **Drift monitor (still-only median)**  
   Background job or inference-loop side task: every 1–2 h compute median `filt940` over recent still samples; compare to the value stored at last successful calibration. Expose delta + status on the dashboard; optionally set a “model stale” flag when the alert threshold is crossed.

3. **Tighter optical checks in `quality.py`**  
   Lower CV threshold (~0.045), add relative peak-to-peak range penalty, lower slope threshold (~2.5). Motion heuristics already work; optical stability is the gap under real contact noise.

4. **Light insert-time validation in `db.py`**  
   Soft-check BPM (e.g. 35–220) and temp sanity on `insert_reading`. Log a warning and optionally clamp extremes rather than failing the insert. SpO₂ < 0 already handled correctly.

### Medium priority (ops & UI)

5. **Schema versioning**  
   Add a `schema_version` table (or PRAGMA user_version) and small migration helpers before adding columns (e.g. lag-corrected estimates, Hailo scores, drift snapshots). `init_db` alone is fine while the schema is stable.

6. **Dashboard delete confirmation**  
   Calibration tab: require an explicit confirm step before `delete_libre` so a fat-fingered id does not drop a good reference.

7. **Cache `count_readings`**  
   Live tab currently counts on every refresh. Cache for 30–60 s (or only recompute every N auto-refreshes). Negligible today; avoids future cost as the table grows.

8. **Log-compression fallback visibility**  
   Rotation already falls back to plain `.1` when `zstd` is missing. Surface a one-line status on the dashboard (or a startup warning metric) so the fallback is not silent forever.

### Lower priority (structure & polish)

9. **Known-issues section in `docs/HAILO_DRIVER.md`**  
   Firmware / DKMS version mismatches, Gen2 vs Gen3 link, venv + system-site-packages notes. Link to Pi / Hailo issues when they appear.

10. **Full `WindowFeatures` at Libre timestamps for multi-feature training**  
    `build_calibration_pairs` currently aggregates a reduced column set. Computing the full feature vector per pair unlocks a stronger multi-feature model once you have enough high-quality still readings. (`train_mlp_onnx.py --from-db` already rebuilds full features.)

11. **Hailo path in the inference loop** — **done in v0.4.2**  
    When `hailo.hef_path` is set and the runner is ready, prefer HEF output and fall back to CPU multi-feature / baseline. See [docs/HAILO_MODEL.md](docs/HAILO_MODEL.md).

12. **Retention / vacuum**  
    On a 250 GB SSD you have headroom, but a simple retention policy (or periodic export + `VACUUM`) keeps the DB tidy after months of continuous logging.

### Testing notes (simulation findings)

Adversarial week-long simulations (contact-loss episodes, bad-still optical noise, temperature swings, slow baseline drift) showed:

- Prefer-still + original quality gates kept nearly 100% of pairs but let drift and residual optical noise into the model (R² collapse over days).
- Adding consecutive-clean + tighter optical checks produced real rejections of contaminated windows while preserving a usable pair set.
- Drift monitoring (still-only median vs early baseline) correctly flagged multi-day baseline shift that no within-window score can see.

These two mechanisms together close the main gaps between “clean desk data” and real wearable use.

## systemd

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

## License

**GNU GPLv3 or later** — see [LICENSE](LICENSE).

⚠️ Experimental only. Not a medical device.
