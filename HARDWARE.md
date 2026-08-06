# Hardware dependency list

Full system: **wearable armband** (firmware in [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm)) + **Pi 5 host** (this repo).

---

## A. Raspberry Pi host (armband-ai)

| Item | Spec / notes | Required? |
|------|----------------|-----------|
| **Raspberry Pi 5** | 4 GB or 8 GB recommended | Yes |
| **Official Pi 5 power supply** | 27 W USB-C (5 V / 5 A) | Yes |
| **microSD** | 32 GB+ (OS + SQLite + models) | Yes |
| **Raspberry Pi AI HAT+** | PCIe → Hailo accelerator | Yes for Hailo path |
| **Hailo silicon** | Confirmed: **HNC18BI11BH** (Hailo-8 industrial, **26 TOPS**). Markings seen: `HAILO / HNC18B1 118H / PHH808.00 / 19DR12 / 2322`. Some 13 TOPS (Hailo-8L) HATs share similar markings — verify with `hailortcli fw-control identify`. | Yes for HEF inference |
| **Network** | Ethernet or Wi‑Fi (same LAN as armband) | Yes |
| **MQTT broker** | Mosquitto on Pi (or another host) | Yes |
| **Optional: USB SSD** | Faster / larger data store than SD | Optional |
| **Optional: case / cooling** | AI HAT + Pi 5 under sustained load | Optional |

### Pi software runtime (not hardware, but blocking)

- Raspberry Pi OS (64-bit)
- Python 3.11+
- Mosquitto MQTT broker
- HailoRT + `hailortcli` + Python bindings (`hailo_platform`) for device identify / HEF

---

## B. Wearable armband (armband-ppg-940nm)

| Item | Spec / notes | Required? |
|------|----------------|-----------|
| **MCU** | Seeed Studio **XIAO ESP32-C3** | Yes |
| **PPG** | **MAX30102** (HR / SpO₂ / temp) | Yes |
| **IMU** | **LIS3DH** — INT1 → XIAO **D2** (motion wake) | Yes |
| **940 nm emitter** | **TSAL6200** IR LED | Yes (experimental channel) |
| **940 nm detector** | **BPW34** photodiode | Yes (experimental channel) |
| **Battery** | 3.7 V LiPo ~500 mAh (e.g. Liter 502535) + JST | Yes |
| **Wire** | 28 AWG silicone | Yes |
| **Connectors** | JST-SH 2/4/6/8/10 pin as needed | Yes |
| **Protoboard** | ~2×8 cm double-sided | Typical |
| **Arm mount** | Elastic armband (e.g. HYS adjustable) | Yes |
| **Solder** | 63/37 rosin-core ~0.8 mm | Build |
| **Optional: OLED** | SSD1306 (used in some firmware builds) | Optional |

See firmware repo **SETUP.md** for pin map, libraries, and first-flash steps.

---

## C. Calibration reference (glucose)

| Item | Notes |
|------|--------|
| **FreeStyle Libre** (or fingerstick meter) | Reference labels for pairing; log via dashboard or `scripts/log_glucose.py` |
| Still posture during samples | Quality gates prefer low motion / high still fraction |

---

## D. Data path (logical)

```
Armband (ESP32-C3)
  → Wi‑Fi → MQTT (topic armband/ppg)
    → Pi logger (SQLite ppg_readings)
      → inference service (quality + baseline/multifeature)
      → Streamlit dashboard
      → optional Hailo-8 HEF when compiled
```

Hailo is **not** on the armband (power / size / PCIe). Inference runs on the Pi AI HAT.

---

## E. Quick checklist before first run

- [ ] Pi 5 booted, on same network as armband
- [ ] Mosquitto listening; `config.yaml` broker/topic match firmware
- [ ] AI HAT seated; `python scripts/hailo_identify.py --extended` succeeds (or document missing runtime)
- [ ] Armband charged, firmware `Armband_Full.ino` with Wi‑Fi/MQTT set
- [ ] LIS3DH INT1 wired to D2
- [ ] Logger + inference + dashboard processes (or systemd units) running
