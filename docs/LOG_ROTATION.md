# Log rotation & compression – error handling

## Defaults

| Setting | Default |
|---------|---------|
| Active log | plain `*.log` |
| Archive | **zstd** → `*.log.1.zst` … |
| Size | 2 MiB (MQTT) / 1 MiB (git auto-pull) |
| Keep | 5 archives |

Config: `logging.compression: zstd | gzip | none`

## Error cases

### 1. `zstd` binary missing (default mode)

| What happens | Detail |
|--------------|--------|
| Rotation | **Still runs** |
| Archive name | Uncompressed `foo.log.1` (not `.zst`) |
| Process | Logger / auto-pull **continues** (no crash) |
| Signal | Warning on stderr / log once at startup if method is zstd |

```bash
# Fix
sudo apt install -y zstd
# then restart logger or wait for next rotation
```

### 2. `zstd` present but compression fails

(disk full, permission denied, corrupt CLI)

| What happens | Detail |
|--------------|--------|
| Attempt | `zstd -f …` fails |
| Fallback | Rename/keep plain `.1` if possible |
| Process | Continues; check disk with `df -h` |

### 3. `gzip` mode, `gzip` missing

Same pattern: plain `.1`, no crash.

### 4. Disk full during MQTT log write

| What happens | Detail |
|--------------|--------|
| Insert path | `DatabaseError` / OS error logged by logger |
| File log | Python logging may also fail to append |
| Recover | Free space on SSD; restart logger |

### 5. Permission denied on `logs/`

| What happens | Detail |
|--------------|--------|
| Startup | May fail creating log file |
| Recover | `mkdir -p logs && chown $USER:$USER logs` |

## Shell scripts

```bash
bash scripts/rotate_logs.sh
# Missing zstd → leaves .1, exit 0

GIT_PULL_LOG_COMPRESSION=zstd bash scripts/git_auto_pull.sh
# Compression failure does not change git exit codes (0/1/2/3)
```

## Check

```bash
which zstd || echo "zstd not installed"
ls -la logs/
zstd -t logs/*.zst 2>/dev/null || true
```
