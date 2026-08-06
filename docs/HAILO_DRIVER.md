# Hailo-8 driver & runtime (Pi 5 + AI HAT+)

Optimized install and verification for the **Raspberry Pi AI HAT+** with Hailo-8 (26 TOPS) or Hailo-8L (13 TOPS).

Official reference: [Raspberry Pi AI software](https://www.raspberrypi.com/documentation/computers/ai.html)

---

## Software stack (what gets installed)

| Layer | Package / module | Role |
|-------|------------------|------|
| **PCIe kernel driver** | `hailo-dkms` / `hailort-pcie-driver` → `hailo_pci` | Talks to the chip over PCIe |
| **Firmware** | `hailofw` / `hailo-firmware` | Loaded onto the NPU at boot |
| **Runtime** | `hailort` | Middleware + **`hailortcli`** |
| **Python bindings** | `python3-hailort` | `import hailo_platform` / `hailort` |
| **TAPPAS core** | `hailo-tappas-core` | GStreamer / vision post-process (optional for armband) |
| **Meta-package** | **`hailo-all`** | Pulls the above for **AI Kit / AI HAT+** (Hailo-8 / 8L) |

**Important:** AI HAT+ **2** (Hailo-10H) uses **`hailo-h10-all`**, not `hailo-all`. Those packages must not be mixed.

On recent Raspberry Pi OS (Trixie+), the PCIe driver is built via **DKMS** (not baked into the kernel). Always install **`dkms` first**.

---

## Recommended install (AI HAT+ / AI Kit)

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot   # if kernel was upgraded

sudo apt install -y dkms
sudo apt install -y hailo-all
sudo reboot
```

### PCIe Gen 3 (bandwidth)

AI HAT+ often auto-configures Gen3 when the GPIO header is seated. For M.2 AI Kit (or if link is Gen2), force Gen3:

```bash
sudo raspi-config
# 6 Advanced Options → A8 PCIe Speed → Yes (Gen 3)
sudo reboot
```

Or in `/boot/firmware/config.txt`:

```text
dtparam=pciex1
dtparam=pciex1_gen=3
```

Then reboot.

### Hardware seating checklist

- Official **27 W** Pi 5 PSU
- AI HAT+ fully seated; **GPIO header** connected (stability / auto Gen3)
- Active cooler recommended under sustained load
- Kernel **≥ 6.6.31** (`uname -r`)

---

## Verify

```bash
# 1. PCIe device present
lspci | grep -i Hailo
# expect: Co-processor: Hailo Technologies Ltd. Hailo-8 AI Processor

# 2. Kernel module loaded
lsmod | grep hailo
# expect: hailo_pci

# 3. Runtime + firmware identity
hailortcli --version
hailortcli fw-control identify

# 4. Optional extended identity (serial / part when available)
hailortcli fw-control identify --extended

# 5. From this repo
cd ~/armband-ai && source .venv/bin/activate
python scripts/hailo_identify.py --extended --save models/hailo_device.json
python scripts/hailo_diagnose.py
```

### Expected `identify` shape

```text
Executing on device: 0001:01:00.0
Identifying board
Control Protocol Version: 2
Firmware Version: x.y.z (...)
Board Name: Hailo-8
Device Architecture: HAILO8          # or HAILO8L for 13 TOPS modules
...
```

**Your silicon (photos 2026-08-06):** package markings consistent with industrial **Hailo-8 / HNC18BI11BH (26 TOPS)**. Always trust `Device Architecture` from `identify` over silk screen alone.

---

## Python venv note

System packages put bindings in the OS Python. For project venvs, either:

```bash
python3 -m venv .venv --system-site-packages
```

or install a matching HailoRT wheel into the venv (from Hailo / Pi packages for your Python version).

`HailoRunner` in this repo tries `import hailo_platform` then `import hailort`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `hailortcli` missing | `sudo apt install hailo-all` + reboot |
| `identify` empty but `lspci` shows Hailo | Reboot after `dkms`+`hailo-all`; check `lsmod \| grep hailo`; `dmesg \| grep -i hailo` |
| Driver not installed / DKMS fail | `sudo apt install dkms linux-headers-$(uname -r)` then reinstall `hailo-all` |
| Kernel too old | `sudo apt full-upgrade` → reboot; need ≥ 6.6.31 |
| Gen2 link / slow | Enable PCIe Gen3 (`raspi-config` or `pciex1_gen=3`) |
| Wrong meta-package | HAT+ / Kit → `hailo-all`; HAT+ 2 (10H) → `hailo-h10-all` only |
| Venv cannot import bindings | Recreate venv with `--system-site-packages` |

---

## armband-ai integration

1. Install stack above until `hailortcli fw-control identify` works.
2. `python scripts/hailo_identify.py --extended --save models/hailo_device.json`
3. Optional: set `hailo.hef_path` in `config.yaml` when you have a compiled `.hef`.
4. `HailoRunner` loads HEF via `hailo_platform` when device + bindings + file are present.

CPU quality + baseline/multifeature models run without Hailo; the NPU is for a future HEF path only.
