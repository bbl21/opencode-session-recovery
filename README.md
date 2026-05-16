# Opencode Session Recovery

[中文](#opencode-session-recovery) | [English](#english)

---

恢复 opencode 文件夹改名/移动后丢失的对话历史。

---

## 问题

当你改名项目文件夹（如 `my-project` → `my-project-v2`），opencode 的对话历史会消失。旧 session 仍在数据库里，但因为 `directory` 字段指向旧的路径，界面不再加载它们。

## 解决方案

```bash
python scripts/recover_sessions.py --relink
```

利用 `session.project_id` → `project.id`（git 根提交哈希，改名不变）将 session 重新关联到新路径，无需手动指定新旧路径。

---

## 快速开始

```bash
# 1. 检查是否有丢失的会话
python scripts/recover_sessions.py --check

# 2. 自动恢复（先预览，再执行）
python scripts/recover_sessions.py --relink --dry-run
python scripts/recover_sessions.py --relink

# 3. 验证恢复结果
python scripts/recover_sessions.py --check
```

如果 `--relink` 无法匹配（session 没有有效的 `project_id`），使用手动模式：

```bash
python scripts/recover_sessions.py --old "path/to/old-name" --new "path/to/new-name"
```

### 跨项目迁移

将某项目最近的 N 条 session 迁移到另一个项目（同时更新 `directory` 和 `project_id`）：

```bash
# 预览
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 3 --dry-run

# 执行（默认迁移最近 5 条）
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target"

# 指定条数
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 10
```

已属于目标项目的 session 自动跳过。子 agent session（`[subagent]`）会标识显示。

---

## 原理

```
session ──project_id──▶ project
  │                       │
  │ (改名后过时)           │ (git 根提交哈希 — 改名不变)
  ▼                       ▼
  directory               worktree (当前路径)
```

### project_id 稳定性

`session.project_id` 引用的是 `project.id`，由 **git 根提交哈希**（`git rev-list --max-parents=0 --all`）派生。这个哈希标识 git 仓库的"起源"，在以下操作中**不会改变**：

- 重命名文件夹
- 移动文件夹到其他位置
- 新增提交、merge、rebase
- 切换分支

仅在仓库"起源"发生变化时**才会改变**：

| 场景 | project_id 是否变化 | 恢复方式 |
|------|---------------------|----------|
| 重命名文件夹（git 仓库） | 否 | `--relink`（自动） |
| 移动文件夹（git 仓库） | 否 | `--relink`（自动） |
| 非 git 目录执行 `git init` | 是 | `--old` / `--new`（手动） |
| 根提交变化（孤立分支、subtree merge） | 是 | `--old` / `--new`（手动） |
| 删除 `.git` 重新初始化 | 是 | `--old` / `--new`（手动） |
| 非 git 目录重命名 | 是 | `--old` / `--new`（手动） |

### 两阶段匹配

1. **第一阶段 — `project_id` 匹配**（安全、确定）：通过 `session.project_id` 关联 `project.id` 获取当前 `worktree` 路径。
2. **第二阶段 — 路径启发式**（需手动确认）：当 `project_id` 缺失或无效时，回退到父目录名匹配。

---

## 参数

| 参数 | 说明 |
|------|------|
| `--check` | 列出路径已失效的 session |
| `--relink` | 通过 `project_id` 自动恢复 |
| `--migrate` | 跨项目迁移最近 N 条 session（更新 `directory` + `project_id`） |
| `--count N` | 迁移条数（默认 5，需配合 `--migrate`） |
| `--old` + `--new` | 手动指定新旧路径（`project_id` 变化时使用） |
| `--dry-run` | 预览模式，不修改数据库 |

---

## 兼容性

| 项目 | 状态 |
|------|------|
| **Windows** | 已验证（Python 3.12） |
| **macOS** | 设计兼容 — 待验证 |
| **Linux** | 设计兼容 — 待验证 |
| **Python** | 3.8+ |
| **依赖** | 仅 Python 标准库（`sqlite3`, `os`, `shutil`, `argparse`） |
| **opencode** | 任意使用 `~/.local/share/opencode/opencode.db` 的版本 |

> macOS/Linux 用户：如遇到问题请 [提交 issue](https://github.com/anomalyco/opencode-session-recovery/issues)。脚本仅使用跨平台 Python 标准库，但仅在 Windows 上测试过。

---

## 安全性

- 修改前自动备份数据库到 `opencode.db.session-recovery-backup`
- `--relink` / `--old`/`--new`：只修改 `session.directory` 字段，不动对话内容
- `--migrate`：额外更新 `session.project_id`，不修改对话内容

---

## 数据库结构参考

| 表 | 关键字段 | 用途 |
|----|---------|------|
| `project` | `id` | git 根提交哈希（改名不变） |
| `project` | `worktree` | 当前项目根路径 |
| `session` | `project_id` → `project.id` | 关联 session 到 git 身份 |
| `session` | `directory` | 创建时的路径（可能过时） |
| `session` | `parent_id` | 非空 = 子 agent session（主列表不显示） |
| `session` | `agent` | Agent 类型（`build`, `explore`, `general` 等） |

---

## 故障排查

### Q: `--relink` 没有匹配到任何 session

这说明 `project_id` 已改变（如非 git → git、根提交变化）。使用 `--old` / `--new` 手动指定新旧路径。

### Q: 恢复后会话仍然不可见

再次运行 `--check` 确认 `directory` 已更新。同时确保重启 opencode 以加载更改。

### Q: 同一父目录下有多个项目

第二阶段（路径启发式）可能产生歧义匹配。始终优先使用 `--relink`（第一阶段），它是确定性的。

### Q: 如何直接查询 project_id？

```bash
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT id, worktree FROM project WHERE worktree = '$(pwd)'"
```

---

## License

MIT

---

<br>
<br>
<br>

---

# English

[中文](#opencode-session-recovery) | [English](#english)

---

Recover lost opencode conversation history after renaming or moving a project folder.

---

## Problem

When you rename a project folder (e.g. `my-project` → `my-project-v2`), opencode's conversation history goes blank. Old sessions still exist in the database but are hidden because their `directory` field points to the old path.

## Solution

```bash
python scripts/recover_sessions.py --relink
```

Uses the stable `project_id` (git root commit hash) to relink sessions to the new path — no manual mapping needed.

---

## Quick Start

```bash
# 1. Check for stale sessions
python scripts/recover_sessions.py --check

# 2. Auto-recover (dry-run first, then execute)
python scripts/recover_sessions.py --relink --dry-run
python scripts/recover_sessions.py --relink

# 3. Verify
python scripts/recover_sessions.py --check
```

If `--relink` cannot match (sessions have no valid `project_id`), use manual mode:

```bash
python scripts/recover_sessions.py --old "path/to/old-name" --new "path/to/new-name"
```

### Cross-project Migration

Migrate the N most recent sessions from one project to another (updates both `directory` and `project_id`):

```bash
# Preview
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 3 --dry-run

# Execute (default: 5 sessions)
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target"

# Custom count
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 10
```

Sessions already in the target project are skipped automatically. Subagent sessions are tagged `[subagent]` for clarity.

---

## How It Works

```
session ──project_id──▶ project
  │                       │
  │ (stale after rename)  │ (git root commit hash — stable)
  ▼                       ▼
  directory               worktree (current path)
```

### project_id Stability

`session.project_id` references `project.id`, which is derived from the **git root commit hash** (`git rev-list --max-parents=0 --all`). This hash identifies the git repository's origin and **does not change** when you:

- Rename the folder
- Move the folder to a different location
- Make new commits, merge, or rebase
- Switch branches

It **will change** only when the repository's origin changes:

| Scenario | project_id Changes? | Recovery Method |
|----------|---------------------|-----------------|
| Rename folder (git repo) | No | `--relink` (auto) |
| Move folder (git repo) | No | `--relink` (auto) |
| `git init` on non-git dir | Yes | `--old` / `--new` (manual) |
| Root commit changes (orphan branch, subtree merge) | Yes | `--old` / `--new` (manual) |
| Delete `.git` and re-init | Yes | `--old` / `--new` (manual) |
| Non-git directory rename | Yes | `--old` / `--new` (manual) |

### Two-Phase Matching

1. **Phase 1 — `project_id` match** (safe, deterministic): Joins `session.project_id` with `project.id` to get the current `worktree` path.
2. **Phase 2 — Path heuristic** (requires manual confirmation): If `project_id` is missing or invalid, falls back to matching by parent directory name.

---

## Arguments

| Flag | Description |
|------|-------------|
| `--check` | List sessions whose directory no longer exists |
| `--relink` | Auto-recover stale sessions via `project_id` |
| `--migrate` | Cross-project migration of N latest sessions (updates `directory` + `project_id`) |
| `--count N` | Number of sessions to migrate (default: 5, requires `--migrate`) |
| `--old` + `--new` | Manual path remap (works when `project_id` changed) |
| `--dry-run` | Preview mode — do not modify database |

---

## Compatibility

| Item | Status |
|------|--------|
| **Windows** | Verified (Python 3.12) |
| **macOS** | Designed for — not yet verified |
| **Linux** | Designed for — not yet verified |
| **Python** | 3.8+ |
| **Dependencies** | Python standard library only (`sqlite3`, `os`, `shutil`, `argparse`) |
| **opencode** | Any version using `~/.local/share/opencode/opencode.db` |

> macOS/Linux users: if you encounter issues, please [report them](https://github.com/anomalyco/opencode-session-recovery/issues). The script uses only cross-platform Python stdlib modules, but has only been tested on Windows.

---

## Safety

- Automatically backs up the database to `opencode.db.session-recovery-backup` before any write
- `--relink` / `--old`/`--new`: only updates `session.directory`, never touches conversation content
- `--migrate`: additionally updates `session.project_id`, never touches conversation content

---

## Database Schema Reference

| Table | Key Column | Purpose |
|-------|-----------|---------|
| `project` | `id` | Git root commit hash (stable across renames) |
| `project` | `worktree` | Current project root path |
| `session` | `project_id` → `project.id` | Links session to git identity |
| `session` | `directory` | Path at session creation (can become stale) |
| `session` | `parent_id` | Non-null = subagent session (hidden from main list) |
| `session` | `agent` | Agent type (`build`, `explore`, `general`, etc.) |

---

## Troubleshooting

### Q: `--relink` shows no matches

This happens when `project_id` has changed (e.g. non-git → git, root commit changed). Use `--old` / `--new` to manually specify the old and new paths.

### Q: Sessions still not visible after recovery

Run `--check` again to verify `directory` was updated. Also ensure opencode is restarted to pick up the changes.

### Q: Multiple projects under the same parent directory

Phase 2 (path heuristic) may produce ambiguous matches. Always use `--relink` (Phase 1) first, which is deterministic.

### Q: How to query project_id directly?

```bash
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT id, worktree FROM project WHERE worktree = '$(pwd)'"
```

---

## License

MIT
