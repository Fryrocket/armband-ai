# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

**v0.4.0** – multi-feature OLS model, HEF-ready `HailoRunner`, quality-gated calibration, background inference service.

## What runs on the Pi

| Service | Command |
|---------|---------|
| MQTT logger | `python scripts/run_logger.py` |
| Inference loop | `python scripts/run_inference.py` |
| Dashboard | `bash scripts/run_dashboard.sh` |

```bash
git pull && source .venv/bin/activate
cp -n config.example.yaml config.yaml
python scripts/run_logger.py & 
python scripts/run_inference.py &
bash scripts/run_dashboard.sh
```

## Calibration & models

```bash
# Log Libre / fingerstick while still
python scripts/log_glucose.py 142

# Linear baseline (filt940 only), quality-gated
python scripts/calibrate.py --min-quality 60 --min-still 0.7

# Multi-feature OLS (needs more pairs)
python scripts/train_multifeature.py --min-quality 60 --save models/multifeature.json
```

Inference prefers `models/multifeature.json` if present, else `models/baseline.json`.

## Hailo-8

Silicon confirmed: **HNC18BI11BH** (26 TOPS).

```bash
python scripts/hailo_identify.py --extended --save models/hailo_device.json
```

Set `hailo.hef_path` in config when you have a compiled `.hef`. `HailoRunner` loads via `hailo_platform` and exposes `infer(feature_vector)`.

## systemd

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

⚠️ Experimental only. Not a medical device.
