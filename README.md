# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion project to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

This repository handles the **Raspberry Pi 5 + AI HAT (Hailo)** side of the system:

- MQTT subscriber that logs data from the armband
- Persistent SQLite storage of every reading
- **Live web dashboard** (graphs on phone)
- Calibration pipeline (940 nm reflectance vs FreeStyle Libre) – *next*
- Model training / inference on Hailo – *later*

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ or AI HAT+ 2 (Hailo)
- Network reachability to the armband’s MQTT broker (usually the Pi itself running Mosquitto)

## Current Status (2026-08-06)

| Component              | Status                                      |
|------------------------|---------------------------------------------|
| Project structure      | Done                                        |
| Config system          | Done (yaml + env overrides)                 |
| MQTT logger            | Working – matches real firmware payload     |
| SQLite schema + WAL    | Done                                        |
| Live dashboard         | **Done** (Streamlit + Plotly)               |
| CSV export             | Done                                        |
| Calibration workflow   | Not started                                 |
| Hailo model pipeline   | Not started                                 |

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

Data lands in `data/armband_data.db` (WAL mode). Logs go to console + `logs/mqtt_logger.log`.

### Environment variable overrides

```bash
export MQTT_BROKER=192.168.1.100
export MQTT_USERNAME=armband
export MQTT_PASSWORD=your_mqtt_pass
export DB_PATH=data/armband_data.db
```

### Export data

```bash
python scripts/export_csv.py                  # last 24 h
python scripts/export_csv.py --minutes 60
python scripts/export_csv.py --all -o full.csv
```

## Dashboard features

- Live metrics: BPM, SpO₂, temperature, filt940, battery
- Time-window selector (5 min → 24 h)
- Stacked charts: 940 nm reflectance, heart rate, SpO₂, battery
- Motion magnitude + transition log
- Auto-refresh every 10 s
- Mobile-friendly layout

## Next concrete steps

1. Confirm exact Hailo board (`hailortcli fw-control identify`)
2. Install / verify Hailo runtime on the Pi 5
3. ~~MQTT logger + persistent storage~~
4. ~~First version of the live dashboard~~
5. Calibration data collection workflow + simple baseline model (filt940 vs Libre)

## Related Repository

- Armband firmware & hardware: https://github.com/Fryrocket/armband-ppg-940nm
