# Data & inference pipeline

End-to-end flow on the Pi 5 host (`armband-ai`).

```
Armband (ESP32-C3)
  │  MQTT JSON  topic: armband/ppg
  ▼
scripts/run_logger.py  →  src/armband_ai/logger.py
  │  insert_reading()
  ▼
SQLite  ppg_readings
  │
  ├──────────────────────────────────────────┐
  ▼                                          ▼
scripts/run_inference.py              dashboard (Live / AI tabs)
  features_from_db()                    load_recent / load_latest
  score_window()                        score_from_db()
  Hailo HEF | MultiFeature | Baseline   estimates + charts
  insert_inference()
  ▼
inference_results
```

## Stages

### 1. Ingest

| Piece | Role |
|-------|------|
| Firmware | Publishes bpm, spo2, temp, motion, moving, raw940, filt940, batt, trans, conn_ms, boot |
| `logger.py` | MQTT subscribe → `insert_reading` → `ppg_readings` |
| `received_at` | UTC ISO timestamp **when the Pi received** the message |

SpO₂ convention: firmware may send **&lt; 0** (typically `-1`) when invalid / not computed. Feature code ignores those values when averaging.

### 2. Features

`features.py` builds a `WindowFeatures` vector over the last N minutes (default 5):

- filt940 mean/std/min/max/slope, raw940 mean
- bpm mean/std, spo2 mean (valid only), temp mean
- motion mean/max, still_fraction, moving_transitions
- batt mean, n_samples, duration_s

### 3. Quality (CPU)

`quality.py` scores 0–100 from still fraction, motion, filt940 stability, sample count, bpm sanity.

Labels: `poor` | `fair` | `good` | `excellent`.

Used to:

- Gate calibration pairs (`min_quality`, `min_still_fraction`)
- Annotate live dashboard / inference rows

### 4. Models

| Model | File | Input |
|-------|------|--------|
| Hailo HEF (optional) | `hailo.hef_path` in config | 17-float feature vector (+ optional norm JSON) |
| Linear baseline | `models/baseline.json` | filt940_mean only |
| Multi-feature OLS | `models/multifeature.json` | subset of WindowFeatures |

**Inference priority** (see `inference_service.py`):

1. **Hailo HEF** — if `hailo.hef_path` is set, file exists, and the runner is ready
2. **CPU multi-feature OLS**
3. **CPU linear baseline**
4. **Quality-only** (no glucose estimate)

Train CPU models:

```bash
python scripts/log_glucose.py 142          # while still
python scripts/calibrate.py --min-quality 60
python scripts/train_multifeature.py --min-quality 60
```

Optional neural path: [HAILO_MODEL.md](HAILO_MODEL.md).

### 5. Inference service

`scripts/run_inference.py` every `inference.interval_seconds` (default 30):

1. Feature window
2. Quality score
3. Predict glucose (Hailo → multi-feature → baseline)
4. Write `inference_results` with `source` = `hailo` | `cpu_multifeature` | `cpu_baseline` | `cpu_quality`

### 6. Hailo (optional)

When `hailo-all` is installed and `hailo.hef_path` points at a compiled `.hef`:

- `HailoRunner` loads via `hailo_platform`
- Feature vector is optionally z-scored with `hailo.norm_path` (or a sibling `*_norm.json`)
- Falls back to CPU models if the device is not ready

See [HAILO_DRIVER.md](HAILO_DRIVER.md).

### 7. Dashboard

| Tab | Uses |
|-----|------|
| Live | Latest PPG, live quality, baseline estimate, charts |
| AI / Features | Feature vector, inference history, Hailo probe |
| Calibration | Libre entry form, quality-gated pairs, fit + save |

## Query helpers

| Function | Module |
|----------|--------|
| `load_recent` / `load_latest` / `count_readings` | `queries.py` |
| `load_libre` / `count_libre` | `queries.py` |
| `load_inference` / `load_latest_inference` | `queries.py` |
| `features_from_db` / `rolling_feature_frames` | `features.py` |
| `build_calibration_pairs` | `calibration.py` |
| `score_from_db` | `quality.py` |

## Not a medical device

Experimental personal research only. Do not use for treatment decisions.
