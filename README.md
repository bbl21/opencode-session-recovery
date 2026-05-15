# Opencode Session Recovery

恢复文件夹改名/移动后丢失的 opencode 对话历史。  
Recover lost opencode conversation history after renaming or moving a project folder.

---

## 问题 / Problem

当你改名项目文件夹（如 `my-project` → `my-project-v2`），opencode 的对话历史会消失。旧 session 仍在数据库里，但因为 `directory` 字段指向旧的路径，界面不再加载它们。

When you rename a project folder, opencode's conversation history goes blank. Old sessions still exist in the database but are hidden because they reference the old directory path.

---

## 解决方案 / Solution

```
python scripts/recover_sessions.py --relink
```

利用 `session.project_id` → `project.id`（git HEAD hash，改名不变）将 session 重新关联到新路径，无需手动指定新旧路径。

Uses the stable git HEAD hash to relink sessions to the new path — no manual mapping needed.

---

## 快速开始 / Quick start

```bash
# 1. 检查 / Check
python scripts/recover_sessions.py --check

# 2. 自动恢复（先预览）/ Auto-recover (dry-run first)
python scripts/recover_sessions.py --relink --dry-run
python scripts/recover_sessions.py --relink

# 3. 验证 / Verify
python scripts/recover_sessions.py --check
```

如果 `--relink` 无法匹配（session 没有 `project_id`），手动指定路径 / If `--relink` can't match, use manual mode:

```bash
python scripts/recover_sessions.py --old "path/to/old-name" --new "path/to/new-name"
```

### 4. 跨项目迁移 / Cross-project migration

将某项目最近的 N 条 session 迁移到另一个项目（更新 `directory` + `project_id`）：

Migrate N latest sessions from one project to another:

```bash
# 预览 / Preview
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 3 --dry-run

# 执行 / Execute（默认迁移最近 5 条）
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target"

# 指定条数 / Custom count
python scripts/recover_sessions.py --migrate --old "path/to/source" --new "path/to/target" --count 10
```

已属于目标项目的 session 自动跳过。子 agent session（`[subagent]`）会标识显示，不会误认为独立对话。

Sessions already in the target project are skipped. Subagent sessions are tagged `[subagent]` for clarity.

---

## 原理 / How it works

```
session ──project_id──▶ project
  │                       │
  │ (改名后过时)           │ (git HEAD hash — 改名不变)
  ▼                       ▼
  directory               worktree (当前路径)
```

`session.project_id` 引用的是 git 仓库的 HEAD hash。**改文件夹名不改变 git 仓库身份**，所以 `project_id` 在改名后依然有效。脚本做的就是：

Renaming a folder doesn't change the git repository identity, so `project_id` remains valid. The script simply does:

```sql
UPDATE session
SET directory = p.worktree
FROM project p
WHERE session.project_id = p.id
  AND session.directory != p.worktree;
```

对于 `project_id = 'global'` 的 session（无 git 关联），回退到同名父目录启发式匹配，需人工确认。

For sessions with no git project association, falls back to parent-directory heuristic (manual confirmation required).

---

## 参数 / Arguments

| Flag | 说明 / Description |
|---|---|---|
| `--check` | 列出路径已失效的 session / List orphaned sessions |
| `--relink` | 通过 project_id 自动恢复 / Auto-recover via project_id |
| `--migrate` | 跨项目迁移最近 N 条 session / Cross-project session migration |
| `--count N` | 迁移条数（默认 5，需配合 `--migrate`）/ Number of sessions to migrate (default: 5) |
| `--old` + `--new` | 手动指定新旧路径 / Manual path remap (works with `--migrate`) |
| `--dry-run` | 预览模式，不修改数据库 / Preview without modifying DB |

---

## 兼容性 / Compatibility

| 项目 | 支持 |
|---|---|
| **OS** | Windows / macOS / Linux |
| **Python** | 3.8+ |
| **依赖** | 仅 Python 标准库（`sqlite3`, `os`, `shutil`, `argparse`） |
| **opencode** | 任意使用 `~/.local/share/opencode/opencode.db` 的版本 |

---

## 跨项目迁移 / Cross-project migration

当两个独立 git 仓库有关联时（例如旧 `CST_MCP` 重命名为 `cst-runtime-cli`，新仓库为 `cst-runtime-cli-dev`），session 有不同的 `project_id`。`--old/--new` 模式只更新 `directory`，session 仍绑定旧项目。`--migrate` 模式同时修复 `directory` 和 `project_id`，使 session 在目标项目下完全可见。

When two separate git repos share a lineage, sessions have different `project_id` values. `--migrate` fixes both `directory` and `project_id`, making sessions fully visible under the target project.

子 agent 产生的 session（`agent` 字段为 `explore`、`general` 等）有非空 `parent_id`，不会作为独立条目显示在 UI 中。`--migrate` 输出会用 `[subagent]` 标记它们并显示父对话标题。

Sessions spawned by subagents have a non-null `parent_id`. `--migrate` tags them with `[subagent]` and shows their parent title.

---

## 安全性 / Safety

- 修改前自动备份数据库到 `opencode.db.session-recovery-backup`
- `--relink` / `--old/--new`：只修改 `session.directory` 字段，不动对话内容
- `--migrate`：额外更新 `session.project_id`，不修改对话内容
- Automatically backs up DB before any write
- `--relink` / `--old/--new`: only touches `session.directory`
- `--migrate`: also updates `session.project_id`, never touches conversation content

---

## License

MIT
