# Hailo-8 model path (train → ONNX → HEF → Pi)

How to take armband `WindowFeatures` from CPU models to a compiled **HEF** on the Raspberry Pi AI HAT+ (Hailo-8 / 26 TOPS).

Companion docs:

| Doc | Role |
|-----|------|
| **[HAILO_DRIVER.md](HAILO_DRIVER.md)** | Driver, firmware, `hailortcli`, bindings |
| **[PIPELINE.md](PIPELINE.md)** | MQTT → DB → features → quality → models |
| This file | Train small MLP, export ONNX, compile HEF, deploy |

⚠️ Experimental only. Not a medical device.

---

## Why Hailo for this project?

| Workload | Best home |
|----------|-----------|
| Linear baseline / multi-feature OLS | **CPU** (already fast) |
| Small MLP / multi-task (glucose + quality) | **Hailo** once data volume justifies it |
| Temporal CNN on raw 940 nm windows | **Hailo** (future) |
| Vision (camera) | **Hailo** (native strength) |

Hailo does not magically improve glucose estimates. More high-quality still Libre pairs + tighter quality gates matter first. Use the NPU when the model is larger than OLS or you want inference off the Pi CPU.

Your silicon: industrial **Hailo-8 (HAILO8)**. HEFs must be compiled for **`hailo8`**, not `hailo8l`.

---

## Feature vector contract (frozen)

`WindowFeatures.to_vector()` default order — **must match train, ONNX, and HEF input**:

```text
0  filt940_mean
1  filt940_std
2  filt940_min
3  filt940_max
4  filt940_slope
5  raw940_mean
6  bpm_mean
7  bpm_std
8  spo2_mean
9  temp_mean
10 motion_mean
11 motion_max
12 still_fraction
13 moving_transitions
14 batt_mean
15 n_samples
16 duration_s
```

Shape: **`[1, 17]`** float32 (batch × features).

Do not reorder without re-exporting ONNX and recompiling the HEF.

---

## End-to-end flow

```text
Pi: quality-gated Libre pairs in SQLite
        │
        ▼
x86 (or Pi CPU): scripts/train_mlp_onnx.py
        → models/glucose_mlp.onnx
        → models/glucose_mlp_norm.json   (mean/std per feature)
        │
        ▼
Linux x86_64 + Hailo Dataflow Compiler (DFC)
        → quantize with calibration vectors
        → compile hw_arch=hailo8
        → models/glucose_mlp.hef
        │
        ▼
Pi: config.yaml  hailo.hef_path: models/glucose_mlp.hef
    inference service prefers Hailo → falls back to CPU
```

**DFC does not run on the Pi.** Compile on an x86_64 Linux machine (or Colab/workstation with the official DFC wheel). Copy only the `.hef` (+ norm JSON) to the Pi.

---

## 1. Collect training data on the Pi

```bash
# Log still Libre readings while logger is running
python scripts/log_glucose.py 142 --notes "still"

# Prefer high quality
python scripts/calibrate.py --min-quality 60 --min-still 0.7 --export-pairs exports/pairs.csv
```

Aim for **≥ 30–50** quality-gated pairs before an MLP is meaningful. More is better.

Export full feature rows (optional helper):

```bash
python scripts/export_features.py --rolling --window 180 --step 60 -o exports/features_rolling.csv
```

For supervised training you need **paired** glucose labels (`build_calibration_pairs` / calibrate export), not unlabeled rolling windows alone.

---

## 2. Train MLP + export ONNX

On a machine with PyTorch (Pi CPU is fine for a tiny net; x86 is faster):

```bash
cd ~/armband-ai && source .venv/bin/activate
pip install torch onnx  # if needed

python scripts/train_mlp_onnx.py \
  --pairs exports/pairs.csv \
  --min-quality 60 \
  --epochs 400 \
  --out-onnx models/glucose_mlp.onnx \
  --out-norm models/glucose_mlp_norm.json
```

Or train directly from the live DB (same gates as calibrate):

```bash
python scripts/train_mlp_onnx.py --from-db --min-quality 60 --min-still 0.7
```

Outputs:

| File | Purpose |
|------|---------|
| `models/glucose_mlp.onnx` | Float graph for DFC |
| `models/glucose_mlp_norm.json` | Per-feature mean/std used at train time |
| Console metrics | Train MAE / RMSE (sanity only) |

The script embeds the **same 17-key order** as `features.py`. Normalization is z-score; apply the same mean/std on the Pi before `HailoRunner.infer()` (or bake norms into ONNX if you prefer).

---

## 3. Compile ONNX → HEF (x86_64 + DFC)

1. Create / log in to [Hailo Developer Zone](https://hailo.ai/developer-zone/) and install **Dataflow Compiler** matching your HailoRT generation (**DFC v3.x for Hailo-8 / 8L**).
2. Prepare a **calibration set**: N×17 float arrays from real still windows (can be the same pairs used for training, or a held-out still export). DFC quantizes using these.
3. Compile with **`hw_arch=hailo8`** (not `hailo8l`).

High-level DFC steps (exact CLI varies by DFC version — follow the official user guide for your install):

```text
parse ONNX → optimize → calibrate (quantization) → compile → glucose_mlp.hef
```

Verify on the build machine if tools allow, then copy to the Pi:

```bash
scp glucose_mlp.hef pi@<pi-ip>:~/armband-ai/models/
scp glucose_mlp_norm.json pi@<pi-ip>:~/armband-ai/models/
```

Check the HEF on the Pi:

```bash
hailortcli parse-hef models/glucose_mlp.hef
# Confirm architecture HAILO8 and input shape compatible with [1,17] (or the shape DFC emitted)
```

---

## 4. Deploy on the Pi

### config.yaml

```yaml
hailo:
  device_json: "models/hailo_device.json"
  hef_path: "models/glucose_mlp.hef"
  norm_path: "models/glucose_mlp_norm.json"   # optional; used by inference if present
  feature_window_minutes: 5
```

### Runtime health

```bash
python scripts/hailo_diagnose.py          # HEALTHY
python scripts/hailo_identify.py --extended --save models/hailo_device.json
```

Venv must see system Hailo bindings:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
```

### Inference priority

`scripts/run_inference.py` / `inference_service` order:

1. **Hailo HEF** (if `hef_path` set, file exists, device + bindings OK, load succeeds) → `source=hailo`
2. CPU multi-feature JSON → `cpu_multifeature`
3. CPU baseline JSON → `cpu_baseline`
4. Quality only → `cpu_quality`

Restart the inference service after setting `hef_path`:

```bash
sudo systemctl restart armband-inference
# or: python scripts/run_inference.py --once
```

---

## 5. `HailoRunner` contract

```python
from armband_ai.hailo import HailoRunner
from armband_ai.features import features_from_db

runner = HailoRunner(hef_path="models/glucose_mlp.hef")
assert runner.ready, runner.status()

feats = features_from_db("data/armband_data.db", minutes=5)
vec = feats.to_vector()   # shape (17,)
# apply norm from glucose_mlp_norm.json if used at train time
out = runner.infer(vec)   # model output ndarray
```

Input must be **float32**, batch dimension added inside `infer()` when needed. Output interpretation depends on the trained head (typically a single glucose mg/dL value).

---

## Checklist

- [ ] `hailortcli fw-control identify` → `Device Architecture: HAILO8`
- [ ] `python scripts/hailo_diagnose.py` → HEALTHY
- [ ] ≥ 30 quality-gated Libre pairs
- [ ] `train_mlp_onnx.py` produced ONNX + norm JSON
- [ ] DFC compiled with **`hailo8`**
- [ ] `hailortcli parse-hef` looks sane on the Pi
- [ ] `config.yaml` → `hailo.hef_path` set
- [ ] Inference row shows `source=hailo` when HEF is live
- [ ] CPU fallback still works if HEF path is cleared or load fails

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `HailoRunner` not ready | Driver/bindings: see [HAILO_DRIVER.md](HAILO_DRIVER.md) |
| HEF load fails | Wrong arch (8 vs 8L), DFC/HailoRT version skew, corrupt file |
| Nonsense glucose | Norm mean/std mismatch; feature key order changed; bad calibration set |
| Venv `import hailo_platform` fails | Recreate venv with `--system-site-packages` |
| DFC not on Pi | Expected — compile on x86_64 only |

---

## Related scripts

| Script | Role |
|--------|------|
| `scripts/train_mlp_onnx.py` | Train tiny MLP from pairs/DB → ONNX + norm JSON |
| `scripts/hailo_identify.py` | Device identity JSON |
| `scripts/hailo_diagnose.py` | Driver / runtime health |
| `scripts/run_inference.py` | Live loop (Hailo → CPU fallback) |
| `scripts/calibrate.py` / `train_multifeature.py` | CPU models (always available) |
