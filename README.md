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

## File index

**Package — `src/armband_ai/`**
- [src/armband_ai/__init__.py](src/armband_ai/__init__.py)
- [src/armband_ai/calibration.py](src/armband_ai/calibration.py) — fingerstick/Libre pairing, build_calibration_pairs, fit_multifeature
- [src/armband_ai/config.py](src/armband_ai/config.py) — YAML config loading and defaults
- [src/armband_ai/db.py](src/armband_ai/db.py) — SQLite writes, insert-time soft validation
- [src/armband_ai/drift_monitor.py](src/armband_ai/drift_monitor.py) — still-only rolling median of filt940 vs baseline
- [src/armband_ai/features.py](src/armband_ai/features.py) — 17-float feature vector, clean streak
- [src/armband_ai/hailo.py](src/armband_ai/hailo.py) — Hailo HEF inference path
- [src/armband_ai/inference_service.py](src/armband_ai/inference_service.py) — CPU/MLP/ONNX/Hailo priority
- [src/armband_ai/logger.py](src/armband_ai/logger.py) — MQTT logger, iOS batch receiver + ACK
- [src/armband_ai/models.py](src/armband_ai/models.py)
- [src/armband_ai/quality.py](src/armband_ai/quality.py) — raw-window quality gates
- [src/armband_ai/queries.py](src/armband_ai/queries.py) — read helpers, init_db

**Dashboard**
- [dashboard/app.py](dashboard/app.py) — Streamlit live dashboard

**Docs**
- [HARDWARE.md](HARDWARE.md)
- [docs/GIT_AUTO_PULL.md](docs/GIT_AUTO_PULL.md)
- [docs/HAILO_DRIVER.md](docs/HAILO_DRIVER.md)
- [docs/HAILO_MODEL.md](docs/HAILO_MODEL.md)
- [docs/LIBRE_FLOW.md](docs/LIBRE_FLOW.md)
- [docs/LOG_ROTATION.md](docs/LOG_ROTATION.md)
- [docs/PIPELINE.md](docs/PIPELINE.md)

**Scripts**
- [scripts/calibrate.py](scripts/calibrate.py)
- [scripts/export_csv.py](scripts/export_csv.py)
- [scripts/export_features.py](scripts/export_features.py)
- [scripts/git_auto_pull.sh](scripts/git_auto_pull.sh)
- [scripts/hailo_diagnose.py](scripts/hailo_diagnose.py)
- [scripts/hailo_identify.py](scripts/hailo_identify.py)
- [scripts/install_git_hooks.sh](scripts/install_git_hooks.sh)
- [scripts/log_glucose.py](scripts/log_glucose.py)
- [scripts/rotate_logs.sh](scripts/rotate_logs.sh)
- [scripts/run_dashboard.sh](scripts/run_dashboard.sh)
- [scripts/run_inference.py](scripts/run_inference.py)
- [scripts/run_logger.py](scripts/run_logger.py)
- [scripts/run_quality.py](scripts/run_quality.py)
- [scripts/train_mlp_onnx.py](scripts/train_mlp_onnx.py)
- [scripts/train_multifeature.py](scripts/train_multifeature.py)
- [scripts/update_file_index.py](scripts/update_file_index.py)

**Systemd units**
- [systemd/armband-dashboard.service](systemd/armband-dashboard.service)
- [systemd/armband-git-pull.service](systemd/armband-git-pull.service)
- [systemd/armband-git-pull.timer](systemd/armband-git-pull.timer)
- [systemd/armband-inference.service](systemd/armband-inference.service)
- [systemd/armband-logger.service](systemd/armband-logger.service)

**Config**
- [.gitignore](.gitignore)
- [LICENSE](LICENSE)
- [config.example.yaml](config.example.yaml)
- [requirements.txt](requirements.txt)

## License

**GNU GPLv3 or later** — see [LICENSE](LICENSE).

⚠️ Experimental only. Not a medical device.
