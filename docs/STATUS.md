# armband-ai status — 2026-08-13 13:15 CDT

Pickup file. Last wrist contact: **2026-08-09**. No S001 measurement yet.

## HEAD

Human work is on `main`. Ignore `[skip ci]` File-index bot commits when citing.

| When | SHA | What |
|------|-----|------|
| 13:15 | *(this commit)* | ASK 21 amend + ASK 24 sidecar |
| earlier | `a63cae7` | ASK 20–23 first landing |
| earlier | `d042baa` | ASK 16–19 motion/spo2 |
| earlier | `2978112` / `7cb852f` / `85f8af3` | v0.5.1 disable + gate |

Package version: **0.5.1**

## Locked today

- Hailo/MLP path **DISABLED** (1121 params; not trainable at pilot scale).
- Quality gate hard-fails: `no_valid_bpm`, `no_valid_spo2`, `no_motion_data`.
- `spo2 > 0` (symmetric with bpm). `moving` NaN → MOVING (`fillna(1)`).
- Frozen 17-vector untouched (`filt940_std` stays). `n_valid_*` gate-side only.
- `fit_baseline` raises on mixed `subject_id` (decision 3).
- Baseline floors **locked**: n≥10 (5×p, 8 residual DoF); glucose range ≥40 **and** all 3 terciles of [min,max] occupied.
- Fits with n<30 are `grade=pilot` in the JSON. Plumbing, not evidence.
- Drop counts: `.attrs` in-process + sibling `pairs.csv.drops.json` via `write_pairs()`.

## Open

| ID | Item |
|----|------|
| 1 | Source population / S001 — **the main event**. Band not on a wrist. |
| 6 | `filt940_std` → `filt940_sd` at **sheet-write boundary only**. Not urgent. |
| 4 | Hailo provenance — deprioritised (path off). |
| 15 | Drive write from this agent is intermittent. Snapshot at `794019d2` is stale vs HEAD. |

## How Claude reviews

Paste **full bare** raw.githubusercontent URLs (Fry must paste them). Prefixes relayed through another agent do not unlock `web_fetch`.

## Next

1. Fry: S001 / put the band on.
2. Recut Drive `08_Source_Snapshot` when write returns.
3. Do not treat any baseline R² at n<30 as a result.

Experimental personal research. **Not a medical device**.
