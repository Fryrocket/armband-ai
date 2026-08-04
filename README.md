# Armband AI – Pi 5 + Hailo Calibration & Live Dashboard

Companion project to [armband-ppg-940nm](https://github.com/Fryrocket/armband-ppg-940nm).

This repository handles the **Raspberry Pi 5 + AI HAT (Hailo)** side of the system:

- MQTT subscriber that logs data from the armband
- Calibration pipeline (940 nm reflectance vs FreeStyle Libre readings)
- Model training / inference (using the Hailo accelerator where it makes sense)
- Live web dashboard with graphs (viewable on iPhone)
- Logging and export tools so AI results can be inspected and iteratively improved

## Hardware

- Raspberry Pi 5
- Raspberry Pi AI HAT+ or AI HAT+ 2 (Hailo accelerator)
- Network reachability to the armband’s MQTT broker

## Current Status

Repository just created. Next concrete steps:

1. Confirm exact Hailo board (photo + `hailortcli fw-control identify`)
2. Install / verify Hailo runtime on the Pi 5
3. MQTT logger + persistent storage of armband data
4. First version of the live dashboard (graphs on phone)
5. Calibration data collection workflow + simple baseline model

## Related Repository

- Armband firmware & hardware: https://github.com/Fryrocket/armband-ppg-940nm
