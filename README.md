# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion project to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

This repository handles the **Raspberry Pi 5 + AI HAT (Hailo)** side of the system:

- MQTT subscriber that logs data from the armband
- Persistent SQLite storage of every reading
- **Live web dashboard** (graphs on phone)
- **Calibration workflow** (Libre / fingerstick vs filt940 + baseline linear model)
- Model training / inference on Hailo – *paused until exact board is known*

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ or AI HAT+ 2 (Hailo) – details pending
- Network reachability to the armband’s MQTT broker (usually the Pi itself running Mosquitto)

## Current Status (2026-08-06)

| Component              | Status                                      |
|------------------------|---------------------------------------------|
| Project structure      | Done                                        |
| Config system          | Done (yaml + env overrides)                 |
| MQTT logger            | Working – matches real firmware payload     |
| SQLite schema + WAL    | Done                                        |
| Live dashboard         | Done (Streamlit + Plotly)                   |
| CSV export             | Done                                        |
| Libre / ref logging    | Done (CLI + dashboard form)                 |
| Calibration pairing    | Done (± window, prefer-still)               |
| Baseline linear model  | Done (numpy OLS, R² / MAE / RMSE)           |
| systemd services       | Done (templates)                            |
| Hailo model pipeline   | Paused – waiting on exact board             |

### MQTT payload expected from firmware

Topic: `armband/ppg`

```json
{
  "bpm": 72,
  "spo2": 98,
  "temp": 36.5,
  "motion": 11.2,
  "moving": true,
  "raw940": 1842,
  "filt940": 1831.4,
  "batt": 3.87,
  "trans": "still_to_moving",
  "conn_ms": 2140,
  "boot": 47
}
```

The logger adds a `received_at` UTC timestamp on the Pi side.

## Quick Start

```bash
# On the Pi 5
git clone https://github.com/Fryrocket/armband-ai.git
cd armband-ai

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Config
cp config.example.yaml config.yaml
# edit config.yaml (broker, credentials, paths) or set env vars

# Terminal 1 – logger
python scripts/run_logger.py

# Terminal 2 – dashboard (reachable from phone)
bash scripts/run_dashboard.sh
# then open http://<pi-ip>:8501 on your iPhone
```

Data lands in `data/armband_data.db` (WAL mode).

### Environment variable overrides

```bash
export MQTT_BROKER=192.168.1.100
export MQTT_USERNAME=armband
export MQTT_PASSWORD=your_mqtt_pass
export DB_PATH=data/armband_data.db
```

## Calibration workflow

1. Wear the armband and keep the logger running.
2. When you scan Libre (or do a fingerstick), log it:

```bash
python scripts/log_glucose.py 142
python scripts/log_glucose.py 118 --source fingerstick --notes "fasting"
python scripts/log_glucose.py 135 --at "2026-08-06T14:30:00"
```

Or use the **Calibration** tab in the dashboard (form at the top).

3. Build pairs and fit the baseline:

```bash
python scripts/calibrate.py
python scripts/calibrate.py --window 120 --save models/baseline.json
python scripts/calibrate.py --export-pairs exports/pairs.csv
```

Pairing looks ±`window` seconds around each Libre timestamp, prefers non-moving samples when available, and averages `filt940` in that window.

4. The Live tab will show a live baseline estimate once `models/baseline.json` exists.

⚠️ **Experimental only. Not a medical device. Do not use for treatment decisions.**

### Export data

```bash
python scripts/export_csv.py                  # last 24 h
python scripts/export_csv.py --minutes 60
python scripts/export_csv.py --all -o full.csv
```

## systemd (optional)

Templates are in `systemd/`. Edit the `User=` and paths, then:

```bash
sudo cp systemd/armband-logger.service /etc/systemd/system/
sudo cp systemd/armband-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger
sudo systemctl enable --now armband-dashboard
```

## Dashboard features

**Live tab**
- Metrics: BPM, SpO₂, temp, filt940, battery
- Live baseline glucose estimate (if model saved)
- Charts + motion + raw table
- Auto-refresh

**Calibration tab**
- Log Libre / fingerstick readings
- View / delete reference readings
- Pairing controls + pair table
- Scatter plot + fit line + R² / MAE / RMSE
- Save baseline model

## Next (when Hailo model is known)

1. Confirm exact board (`hailortcli fw-control identify`)
2. Install / verify Hailo runtime on the Pi 5
3. Decide what runs on the accelerator vs CPU (e.g. richer temporal model)

## Related Repository

- Armband firmware & hardware: https://github.com/Fryrocket/armband-ppg-940nm
