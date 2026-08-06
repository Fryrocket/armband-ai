# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

**v0.4.1** – multi-feature OLS, HEF-ready `HailoRunner`, quality-gated calibration, inference service, pipeline docs, DB hardening.

| Doc | Purpose |
|-----|---------|
| **[HARDWARE.md](HARDWARE.md)** | BOM: Pi, AI HAT, armband, boot SSD |
| **[docs/HAILO_DRIVER.md](docs/HAILO_DRIVER.md)** | Driver / firmware / HailoRT install & diagnose |
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

```bash
python scripts/log_glucose.py 142 --notes "still"
python scripts/calibrate.py --min-quality 60 --min-still 0.7
python scripts/train_multifeature.py --min-quality 60
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

| \|\u0394 median\| vs last cal | Action |
|--------------------------|--------|
| ≳ 40 | Normal / mild warn |
| ≳ 80 | Alert — consider re-calibration |
| sustained large shift | Mark model stale; collect new still Libre pairs |

Surface the current delta on the dashboard AI / Calibration tabs. Re-run `calibrate.py` / `train_multifeature.py` after an alert once you have fresh high-quality pairs.

## systemd

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

⚠️ Experimental only. Not a medical device.
