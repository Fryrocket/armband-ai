# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

**v0.4.0** – multi-feature OLS, HEF-ready `HailoRunner`, quality-gated calibration, inference service.

| Doc | Purpose |
|-----|---------|
| **[HARDWARE.md](HARDWARE.md)** | BOM: Pi, AI HAT, armband |
| **[docs/HAILO_DRIVER.md](docs/HAILO_DRIVER.md)** | **Driver / firmware / HailoRT install & diagnose** |

## Hailo-8 driver (short path)

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y dkms hailo-all
sudo reboot

hailortcli fw-control identify
python scripts/hailo_diagnose.py
python scripts/hailo_identify.py --extended --save models/hailo_device.json
```

- Meta-package **`hailo-all`** = PCIe DKMS driver + firmware + HailoRT + Python bindings (AI HAT+ / AI Kit).
- **`hailo-h10-all`** is only for AI HAT+ 2 (Hailo-10H) — do not mix.
- Enable **PCIe Gen3** via `raspi-config` if the link is Gen2.

Silicon (photos): industrial **Hailo-8 / HNC18BI11BH (26 TOPS)** — confirm with `Device Architecture` from `identify`.

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
python scripts/log_glucose.py 142
python scripts/calibrate.py --min-quality 60 --min-still 0.7
python scripts/train_multifeature.py --min-quality 60 --save models/multifeature.json
```

## systemd

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armband-logger armband-inference armband-dashboard
```

⚠️ Experimental only. Not a medical device.
