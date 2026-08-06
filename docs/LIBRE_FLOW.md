# Libre / reference glucose workflow

How reference labels get into SQLite and into models.

## What gets stored

Table `libre_readings`:

| Column | Meaning |
|--------|---------|
| `recorded_at` | When the **glucose sample** was taken (UTC ISO) |
| `glucose_mgdl` | Value in mg/dL |
| `source` | `libre` \| `fingerstick` \| `other` |
| `notes` | Free text |
| `created_at` | When the row was inserted on the Pi |

Pairing with PPG uses **`recorded_at`**, not `created_at`. Log the reading as close as possible to the moment you look at the Libre/meter, while the armband is **still**.

## Ways to enter a reading

### A. CLI (preferred when SSH’d to the Pi)

```bash
# Now (UTC)
python scripts/log_glucose.py 142

# With note
python scripts/log_glucose.py 142 --notes "post-meal 45 min"

# Fingerstick
python scripts/log_glucose.py 118 --source fingerstick

# Backfill a past timestamp (UTC)
python scripts/log_glucose.py 135 --at "2026-08-06T14:30:00"
```

### B. Dashboard Calibration tab

1. Open `http://<pi-ip>:8501`
2. **Calibration** tab → form → glucose, source, optional notes → Save
3. Timestamp = **now (UTC)** (same as CLI without `--at`)

### C. Programmatic

```python
from armband_ai.db import insert_libre
insert_libre(db_path, glucose_mgdl=142, source="libre", notes="still")
```

There is no separate REST API; use CLI, UI, or Python.

## How pairs are built

`build_calibration_pairs()` for each Libre row:

1. Find `ppg_readings` with `received_at` in ± `window_seconds` (default 180)
2. Prefer still samples if any exist
3. Drop if `still_fraction` &lt; `min_still_fraction`
4. Score quality on the candidate window; drop if &lt; `min_quality`
5. Aggregate filt940 mean, etc.

Then:

```bash
python scripts/calibrate.py --min-quality 60 --min-still 0.7
python scripts/train_multifeature.py --min-quality 60
```

## Best practices

1. Sit still 1–2 minutes before logging
2. Log Libre **while** the armband is streaming (logger running)
3. Use quality gates; don’t loosen them just to get more pairs of junk
4. Need ≥ 2 pairs for linear baseline; more for multi-feature
5. Delete bad entries in the Calibration tab (by id) or re-log with correct `--at`

## SpO₂ note

SpO₂ on PPG rows is independent of Libre. Invalid SpO₂ is stored as **&lt; 0** (usually `-1`) and is **not** used in feature averages.
