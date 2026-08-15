# armband-ai status — 2026-08-15 ~13:15 CDT

Pickup file. Last wrist contact: **2026-08-09**. No S001 measurement yet.

## HEAD

Human work is on `main`. Ignore `[skip ci]` File-index bot commits when citing.

| When | SHA | What |
|------|-----|------|
| 2026-08-15 | *(this commit)* | Response to Claude S001 Pre-Launch Review; confirmed n≥10 guard already live |
| 2026-08-13 13:15 | `6ca5290` (bot) / prior human | ASK 21 amend + ASK 24 sidecar |
| earlier | `a63cae7` | ASK 20–23 first landing |
| earlier | `d042baa` | ASK 16–19 motion/spo2 |
| earlier | `2978112` / `7cb852f` / `85f8af3` | v0.5.1 disable + gate |

Package version: **0.5.1**

## Locked (unchanged)

- Hailo/MLP path **DISABLED** (1121 params; not trainable at pilot scale).
- Quality gate hard-fails: `no_valid_bpm`, `no_valid_spo2`, `no_motion_data`.
- `spo2 > 0` (symmetric with bpm). `moving` NaN → MOVING (`fillna(1)`).
- Frozen 17-vector untouched (`filt940_std` stays). `n_valid_*` gate-side only.
- `fit_baseline` raises on mixed `subject_id` (decision 3).
- Baseline floors **locked and enforced in code**: n≥10 (5×p, 8 residual DoF); glucose range ≥40 **and** all 3 terciles of [min,max] occupied.
- Fits with n<30 are `grade=pilot` in the JSON. Plumbing, not evidence.
- Drop counts: `.attrs` in-process + sibling `pairs.csv.drops.json` via `write_pairs()`.

## Claude S001 Pre-Launch Review (2026-08-15 18:01 UTC) — Disposition

Claude flagged three blockers. Disposition:

1. **Republish Bench Desk** — still open. Requires Fry/Grok platform action on `lark-able-turbo-drift.grok.me`. Cannot be done from agent tools.
2. **Verify deployed Pi inference config** — still open. Example config has empty `hef_path` (safe). Real deployed file must be cat’ed by Fry and pasted. Code already skips empty/missing HEF.
3. **Hard floor in `fit_baseline`** — **already present**. Guard at top of function raises on `n < 10`. No further code change.

Only 1 and 2 remain as S001 gates.

## Open

| ID | Item |
|----|------|
| 1 | Source population / S001 — **the main event**. Band not on a wrist. |
| Desk | Republish preview → live on lark-able-turbo-drift.grok.me |
| Config | Fry pastes real Pi `config.yaml` (or HEF section) |
| 6 | `filt940_std` → `filt940_sd` at **sheet-write boundary only**. Not urgent. |
| 15 | Drive write from this agent is intermittent. Snapshot at `794019d2` is stale vs HEAD. |

## How Claude reviews

Paste **full bare** raw.githubusercontent URLs (Fry must paste them). Prefixes relayed through another agent do not unlock `web_fetch`.

## Next

1. Fry: republish Desk app + confirm Session Start form fields.
2. Fry: SSH → cat deployed config → paste HEF section (or whole file).
3. Once 1+2 clear → S001 (band on wrist, log pairs via Desk, export CSV).
4. Recut Drive `08_Source_Snapshot` when convenient (non-blocking).

Experimental personal research. **Not a medical device**.
