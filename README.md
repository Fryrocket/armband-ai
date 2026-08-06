# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion project to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

This repository handles the **Raspberry Pi 5 + AI HAT (Hailo)** side of the system:

- MQTT subscriber that logs data from the armband
- Persistent SQLite storage of every reading
- Calibration pipeline (940 nm reflectance vs FreeStyle Libre readings) – *coming*
- Model training / inference (Hailo accelerator where it makes sense) – *coming*
- Live web dashboard with graphs (viewable on iPhone) – *coming*

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ or AI HAT+ 2 (Hailo)
- Network reachability to the armband’s MQTT broker (usually the Pi itself running Mosquitto)

## Current Status (2026-08-06)

| Component              | Status                          |
|------------------------|---------------------------------|
| Project structure      | Done                            |
| Config system          | Done (yaml + env overrides)     |
| MQTT logger            | Working – matches real firmware payload |
| SQLite schema + WAL    | Done                            |
| Live dashboard         | Not started                     |
| Calibration workflow   | Not started                     |
| Hailo model pipeline   | Not started                     |

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

# Run the logger
python scripts/run_logger.py
```

Data lands in `data/armband_data.db` (WAL mode). Logs go to console + `logs/mqtt_logger.log`.

### Environment variable overrides

```bash
export MQTT_BROKER=192.168.1.100
export MQTT_USERNAME=armband
export MQTT_PASSWORD=your_mqtt_pass
export DB_PATH=data/armband_data.db
```

## Next concrete steps

1. Confirm exact Hailo board (`hailortcli fw-control identify`)
2. Install / verify Hailo runtime on the Pi 5
3. ~~MQTT logger + persistent storage~~ ← done
4. First version of the live dashboard (graphs on phone)
5. Calibration data collection workflow + simple baseline model (filt940 vs Libre)

## Related Repository

- Armband firmware & hardware: https://github.com/Fryrocket/armband-ppg-940nm
