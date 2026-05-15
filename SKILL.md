---
name: opencode-session-recovery
description: "Recover lost opencode conversations after folder rename/move. Auto-relinks stale session.directory pointers via project_id (git HEAD hash). Supports --check, --relink, and manual --old/--new remap. Works on Windows, macOS, Linux."
---

# Opencode Session Recovery Skill

Recovers conversation history that becomes inaccessible after renaming or moving a project folder.

## How it works

opencode stores conversations in `~/.local/share/opencode/opencode.db`. Each session has a `directory` field pointing to the project root at creation time. When you rename the folder, opencode can't find old sessions because the path doesn't match.

But `session.project_id` references `project.id` — a git HEAD hash that **stays the same across renames**. This skill uses that stable relationship to relink sessions to the new path, no guessing required.

## Usage

### 1. Check for stale sessions

```bash
python scripts/recover_sessions.py --check
```

### 2. Auto-recover (recommended)

```bash
# Preview
python scripts/recover_sessions.py --relink --dry-run

# Execute
python scripts/recover_sessions.py --relink
```

Phase 1 uses `project_id` matching (safe, deterministic). Phase 2 falls back to path heuristic (requires manual confirmation).

### 3. Manual recover

When you know both the old and new paths:

```bash
python scripts/recover_sessions.py --old "path/to/old-name" --new "path/to/new-name" --dry-run
python scripts/recover_sessions.py --old "path/to/old-name" --new "path/to/new-name"
```

### 4. Verify

```bash
python scripts/recover_sessions.py --check
```

## Arguments

| Argument | Description |
|---|---|
| `--check` | List sessions whose directory no longer exists |
| `--relink` | Auto-match stale sessions to current worktrees |
| `--old` | Old / stale project path (manual mode) |
| `--new` | New / current project path (manual mode) |
| `--dry-run` | Preview only — do not modify database |

## Why this is needed

The `session` table stores `directory` at creation time and never updates it. The `project` table, however, is kept current. When you rename a folder:

- `project.worktree` is updated (or a new project row is created with the same `project_id`)
- `session.directory` still points to the old path
- opencode's UI queries sessions by `directory`, so old conversations are hidden

This skill simply does `UPDATE session SET directory = project.worktree WHERE session.project_id = project.id` — exactly what opencode itself could, but currently doesn't, do.

## Database reference

| Table | Key column | Purpose |
|---|---|---|
| `session` | `project_id` → `project.id` | Links to git identity (stable across renames) |
| `session` | `directory` | Path at session creation (can become stale) |
| `project` | `worktree` | Current project root path |
| `project` | `id` | git HEAD hash (unchanged by rename) |
