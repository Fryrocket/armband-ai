# Hardware dependency list

Full system: **wearable armband** ([armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm)) + **Pi 5 host** (this repo).

**Hailo driver install / verify:** see **[docs/HAILO_DRIVER.md](docs/HAILO_DRIVER.md)**.

---

## A. Raspberry Pi host (armband-ai)

| Item | Spec / notes | Required? |
|------|----------------|-----------|
| **Raspberry Pi 5** | 4 GB or 8 GB recommended | Yes |
| **Official Pi 5 power supply** | 27 W USB-C (5 V / 5 A) | Yes |
| **microSD** | 32 GB+ | Yes |
| **Raspberry Pi AI HAT+** | PCIe Hailo accelerator (13 or 26 TOPS) | Yes for NPU |
| **Hailo silicon** | Confirmed markings → industrial **Hailo-8 (HNC18BI11BH, 26 TOPS)**. Verify with `hailortcli fw-control identify` (`Device Architecture: HAILO8` vs `HAILO8L`). | Yes for HEF |
| **Active cooler** | Recommended with AI HAT under load | Strongly recommended |
| **Network** | Same LAN as armband | Yes |
| **MQTT broker** | Mosquitto on Pi (typical) | Yes |

### Hailo software stack (driver details)

| Component | How you get it |
|-----------|----------------|
| DKMS + PCIe driver (`hailo_pci`) | `sudo apt install dkms` then `hailo-all` |
| Firmware | pulled by `hailo-all` |
| HailoRT + `hailortcli` | `hailo-all` |
| Python bindings (`python3-hailort`) | `hailo-all` |
| TAPPAS core | optional for this project; included in `hailo-all` |

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y dkms hailo-all
sudo reboot
hailortcli fw-control identify
python scripts/hailo_diagnose.py
```

Use **`hailo-all`** for AI HAT+ / AI Kit. Use **`hailo-h10-all`** only for AI HAT+ 2 (Hailo-10H) — do not mix.

PCIe Gen3: `raspi-config` → Advanced → PCIe Speed → Gen 3 (or `dtparam=pciex1_gen=3` in `/boot/firmware/config.txt`).

---

## B. Wearable armband (armband-ppg-940nm)

| Item | Spec / notes | Required? |
|------|----------------|-----------|
| **MCU** | Seeed **XIAO ESP32-C3** | Yes |
| **PPG** | **MAX30102** | Yes |
| **IMU** | **LIS3DH** — INT1 → XIAO **D2** | Yes |
| **940 nm emitter** | **TSAL6200** | Yes (experimental) |
| **940 nm detector** | **BPW34** | Yes (experimental) |
| **Battery** | 3.7 V LiPo ~500 mAh + JST | Yes |
| **Wire / connectors** | 28 AWG silicone, JST-SH | Yes |
| **Mount** | Elastic armband | Yes |
| **Optional OLED** | SSD1306 | Optional |

---

## C. Calibration reference

| Item | Notes |
|------|--------|
| FreeStyle Libre or fingerstick | Labels for `log_glucose` / dashboard |
| Still posture | Quality gates prefer high still fraction |

---

## D. Data path

```
Armband (ESP32-C3) → Wi-Fi → MQTT (armband/ppg)
  → Pi logger (SQLite)
  → inference service (CPU quality + baseline/multifeature)
  → optional Hailo-8 HEF (after driver + compiled model)
  → Streamlit dashboard
```

Hailo is **not** on the armband (PCIe / power). NPU stays on the Pi AI HAT.

---

## E. Checklist

- [ ] Pi 5 + 27 W PSU + AI HAT seated (GPIO connected)
- [ ] `dkms` + `hailo-all` installed; reboot
- [ ] `lspci | grep -i Hailo` and `lsmod | grep hailo` OK
- [ ] `hailortcli fw-control identify` prints architecture
- [ ] `python scripts/hailo_diagnose.py` → HEALTHY
- [ ] Mosquitto + armband MQTT config match
- [ ] Logger / inference / dashboard running
