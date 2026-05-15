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

### 4. Migrate N latest sessions (cross-project)

Move the N most recent sessions from one project to another, updating both `directory` and `project_id`:

```bash
# Preview
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 3 --dry-run

# Execute (migrate 5 latest sessions, default)
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target"

# Execute (migrate 2 latest)
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 2
```

Sessions that already belong to the target project are skipped automatically. Output distinguishes subagent sessions (`[subagent]` tag) from top-level conversations.

### 5. Verify

```bash
python scripts/recover_sessions.py --check
```

## Arguments

| Argument | Description |
|---|---|
| `--check` | List sessions whose directory no longer exists |
| `--relink` | Auto-match stale sessions to current worktrees |
| `--migrate` | Migrate N latest sessions from `--old` to `--new` (updates directory + project_id) |
| `--count N` | Number of latest sessions to migrate (default: 5, only with `--migrate`) |
| `--old` | Old / stale project path (manual or migrate mode) |
| `--new` | New / current project path (manual or migrate mode) |
| `--dry-run` | Preview only — do not modify database |

## Why this is needed

The `session` table stores `directory` at creation time and never updates it. The `project` table, however, is kept current. When you rename a folder:

- `project.worktree` is updated (or a new project row is created with the same `project_id`)
- `session.directory` still points to the old path
- opencode's UI queries sessions by `directory`, so old conversations are hidden

This skill simply does `UPDATE session SET directory = project.worktree WHERE session.project_id = project.id` — exactly what opencode itself could, but currently doesn't, do.

## Cross-project migration

When two separate git repos share a lineage (e.g., old `CST_MCP` renamed to `cst-runtime-cli` vs. new `cst-runtime-cli-dev`), sessions have different `project_id` values. The `--old`/`--new` mode updates only `directory`, leaving the session bound to the old project. The `--migrate` mode fixes both `directory` and `project_id`, making sessions fully visible under the target project.

## Subagent session distinction

Sessions spawned by subagents (`agent` field = `explore`, `general`, etc.) have a non-null `parent_id` linking them to their parent session. These sub-sessions are **not displayed** as independent entries in opencode's UI. `--migrate` output marks them with a `[subagent]` tag and shows their parent title, so you know which sessions will actually appear as top-level conversations.

## Database reference

| Table | Key column | Purpose |
|---|---|---|
| `session` | `project_id` → `project.id` | Links to git identity (stable across renames) |
| `session` | `directory` | Path at session creation (can become stale) |
| `session` | `parent_id` | Non-null = subagent sub-session (hidden from main list) |
| `session` | `agent` | Agent type (`build`, `explore`, `general`, etc.) |
| `project` | `worktree` | Current project root path |
| `project` | `id` | git HEAD hash (unchanged by rename) |
