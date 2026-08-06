# Git auto-pull – error handling examples

Scripts: `scripts/git_auto_pull.sh`, `scripts/install_git_hooks.sh`.

## Exit codes (`git_auto_pull.sh`)

| Code | Meaning | Typical cause |
|------|---------|----------------|
| **0** | Success **or intentional skip** | Already up to date, dirty tree, no `origin/branch` |
| **1** | Local / repo error | Not a git repo, detached HEAD, no `git`, can't write `logs/` |
| **2** | Network / remote | No `origin`, `git fetch` failed (offline, auth, DNS) |
| **3** | Pull / rebase failure | Conflict; script tries `git rebase --abort` |

Log file: `logs/git_auto_pull.log` (UTC timestamps).

---

## Example: success

```bash
$ bash scripts/git_auto_pull.sh
2026-08-06T15:00:01Z auto-pull start
2026-08-06T15:00:01Z branch=main
2026-08-06T15:00:02Z local=a1b2c3d remote=e4f5g6h – pull --rebase
2026-08-06T15:00:03Z ok now at e4f5g6h
$ echo $?
0
```

---

## Example: already up to date (exit 0)

```bash
$ bash scripts/git_auto_pull.sh
2026-08-06T15:01:00Z auto-pull start
2026-08-06T15:01:00Z branch=main
2026-08-06T15:01:01Z already up to date (e4f5g6h)
$ echo $?
0
```

---

## Example: dirty working tree – skip (exit 0)

Local edits are **not** overwritten.

```bash
$ echo "# temp" >> README.md
$ bash scripts/git_auto_pull.sh
2026-08-06T15:02:00Z auto-pull start
2026-08-06T15:02:00Z branch=main
2026-08-06T15:02:00Z dirty working tree – skip pull
 M README.md
$ echo $?
0
```

**Recover:** commit, stash, or discard, then pull again.

```bash
git status
git stash push -m "wip"    # or: git checkout -- README.md
bash scripts/git_auto_pull.sh
git stash pop              # if you stashed
```

---

## Example: not a git repo (exit 1)

```bash
$ bash /wrong/path/scripts/git_auto_pull.sh
2026-08-06T15:03:00Z auto-pull start
2026-08-06T15:03:00Z ERROR: not a git repo: /wrong/path
$ echo $?
1
```

**Recover:**

```bash
cd ~/armband-ai   # real clone path
bash scripts/git_auto_pull.sh
```

---

## Example: detached HEAD (exit 1)

```text
ERROR: detached HEAD – checkout a branch first (e.g. git checkout main)
```

**Recover:**

```bash
git checkout main
bash scripts/git_auto_pull.sh
```

---

## Example: fetch / network failure (exit 2)

```text
ERROR: git fetch origin failed (network or auth?)
```

or

```text
ERROR: remote 'origin' not configured
```

**Recover:**

```bash
# connectivity
ping -c 2 github.com

# remote URL
git remote -v
git remote add origin https://github.com/Fryrocket/armband-ai.git   # if missing
# or fix URL:
git remote set-url origin https://github.com/Fryrocket/armband-ai.git

bash scripts/git_auto_pull.sh
```

---

## Example: rebase conflict (exit 3)

Script attempts `git rebase --abort` so you are not left mid-rebase.

```text
... CONFLICT ...
ERROR: rebase conflict – aborting rebase to leave repo clean
ERROR: git pull --rebase failed (exit 1). Resolve conflicts manually if needed.
```

**Recover (manual resolve):**

```bash
git status
# if still rebasing (rare if abort worked):
git rebase --abort

# option A – keep local commits, rebase onto origin
git fetch origin
git rebase origin/main
# fix files, then:
git add -A
git rebase --continue

# option B – discard local commits (destructive)
# git reset --hard origin/main
```

---

## Shell patterns

### Check exit code after pull

```bash
if bash scripts/git_auto_pull.sh; then
  echo "pull ok or skipped"
else
  rc=$?
  echo "pull failed rc=$rc"
  case $rc in
    1) echo "fix local repo / HEAD" ;;
    2) echo "check network and git remote -v" ;;
    3) echo "conflict – see logs/git_auto_pull.log" ;;
  esac
  tail -20 logs/git_auto_pull.log
fi
```

### Cron / systemd: only restart services on real update

`git_auto_pull.sh` exits 0 both when it updated **and** when it skipped. To detect a real change:

```bash
before=$(git rev-parse HEAD)
bash scripts/git_auto_pull.sh || exit $?
after=$(git rev-parse HEAD)
if [[ "$before" != "$after" ]]; then
  echo "Updated $before → $after – restart services if needed"
  # sudo systemctl restart armband-logger armband-inference
fi
```

### Installer failures

```bash
bash scripts/install_git_hooks.sh --timer
# exit 1 → git/repo/hooks problem
# exit 2 → systemctl / user timer problem

# if user timer fails:
sudo loginctl enable-linger "$USER"
bash scripts/install_git_hooks.sh --timer
```

### Journal (timer)

```bash
journalctl --user -u armband-git-pull.service -n 50 --no-pager
tail -50 ~/armband-ai/logs/git_auto_pull.log
```

---

## Quick reference

```bash
cd ~/armband-ai
bash scripts/git_auto_pull.sh; echo exit=$?
tail -30 logs/git_auto_pull.log
git status
git remote -v
```
