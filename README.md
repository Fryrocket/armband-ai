# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion project to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

**v0.3.0** – quality-gated calibration + background inference service.

This repository handles the **Raspberry Pi 5 + AI HAT (Hailo)** side of the system:

- MQTT subscriber that logs data from the armband
- Persistent SQLite storage of every reading
- Live web dashboard (graphs on phone)
- Calibration workflow with **quality / still gates**
- Background inference service (quality + baseline estimate → DB)
- Feature extraction + Hailo-8 device identity / inference stubs

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ (Hailo-8)
- Network reachability to the armband’s MQTT broker (usually the Pi itself running Mosquitto)

### Confirmed Hailo silicon (2026-08-06)

```
HAILO / HNC18B1 118H / PHH808.00 / 19DR12 / 2322
→ industrial Hailo-8 (HNC18BI11BH), 26 TOPS
```

Some 13 TOPS (Hailo-8L) HATs ship with the same marking — trust `hailortcli fw-control identify`.

## Quick Start

```bash
git clone https://github.com/Fryrocket/armband-ai.git
cd armband-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit broker / paths

# Terminal 1 – MQTT logger
python scripts/run_logger.py

# Terminal 2 – quality + baseline every 30s
python scripts/run_inference.py

# Terminal 3 – dashboard
bash scripts/run_dashboard.sh
# open http://<pi-ip>:8501
```

## Services

| Script | Role |
|--------|------|
| `scripts/run_logger.py` | MQTT → `ppg_readings` |
| `scripts/run_inference.py` | features → quality → baseline → `inference_results` |
| `scripts/run_quality.py` | one-shot quality print |
| `scripts/calibrate.py` | quality-gated pairs + baseline fit |
| `scripts/hailo_identify.py` | probe Hailo device |
| `scripts/export_features.py` | CSV/JSON feature windows |

### systemd

```bash
sudo cp systemd/armband-logger.service /etc/systemd/system/
sudo cp systemd/armband-inference.service /etc/systemd/system/
sudo cp systemd/armband-dashboard.service /etc/systemd/system/
# edit User= and paths if needed
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

## Calibration (quality-gated)

Default gates (config / CLI / dashboard):

- `min_quality: 50` (0–100 CPU heuristic)
- `min_still_fraction: 0.6`
- prefer still samples inside the ±window

```bash
python scripts/log_glucose.py 142
python scripts/calibrate.py --min-quality 60 --min-still 0.7 --save models/baseline.json
```

Pairs that fail the gates are dropped before the linear fit.

## Inference service

Every `inference.interval_seconds` (default 30):

1. Build feature window (`window_minutes`, default 5)
2. Score quality
3. If `models/baseline.json` exists, predict glucose from `filt940_mean`
4. Insert into `inference_results`

```bash
python scripts/run_inference.py --once
python scripts/run_inference.py --interval 15 --window 3
```

## Hailo next steps

1. `python scripts/hailo_identify.py --extended --save models/hailo_device.json`
2. Install HailoRT on the Pi if needed
3. Collect still + Libre pairs; export features; train; compile HEF
4. Set `hailo.hef_path` and implement `HailoRunner.infer()`

## Related

- Firmware: https://github.com/Fryrocket/armband-ppg-940nm
