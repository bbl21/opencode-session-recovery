#!/usr/bin/env python3
"""
opencode-session-recovery — Relink orphaned sessions after folder rename/move.

Recovers opencode conversation history that becomes inaccessible when a
project folder is renamed or moved. Uses the session ↔ project relationship
in opencode's SQLite database to relink stale directory paths.

For opencode Skill usage, see ../SKILL.md.
For standalone usage: python recover_sessions.py --help
"""

import argparse
import os
import shutil
import sqlite3
import sys
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ── constants ────────────────────────────────────────────────────────

DB_PATH = str(Path.home() / ".local" / "share" / "opencode" / "opencode.db")
BACKUP_SUFFIX = ".session-recovery-backup"
BACKUP_PATH = DB_PATH + BACKUP_SUFFIX

OK = "\u2713"   # ✓
ERR = "\u2717"  # ✗


# ── data classes ─────────────────────────────────────────────────────

@dataclass
class StaleSession:
    """A session whose directory no longer exists on disk."""
    id: str
    title: Optional[str]
    directory: str
    project_id: Optional[str]
    time_created: Optional[int]
    time_updated: Optional[int]


@dataclass
class MatchResult:
    """A matched session with old and new directory paths."""
    session_id: str
    title: str
    old_directory: str
    new_directory: str


@dataclass
class SessionInfo:
    """Metadata about a session's agent type and parent relationship."""
    agent: str
    is_subagent: bool
    parent_title: Optional[str]


# ── helpers ──────────────────────────────────────────────────────────

def eprint(*args, **kwargs):
    """Print to stderr, safe for piped output."""
    print(*args, file=sys.stderr, **kwargs)


def format_timestamp(ts_ms: Optional[int]) -> str:
    """Convert millisecond timestamp to human-readable string."""
    if ts_ms is None:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "N/A"


def ensure_db() -> bool:
    """Return True if database exists and is readable."""
    path = Path(DB_PATH)
    if not path.exists():
        eprint(f"[{ERR}] Database not found: {DB_PATH}")
        eprint("    Expected at ~/.local/share/opencode/opencode.db")
        return False
    if not os.access(str(path), os.R_OK):
        eprint(f"[{ERR}] No read permission: {DB_PATH}")
        return False
    return True


def backup_db() -> None:
    """Create a backup of the database before any write operation."""
    if os.path.exists(BACKUP_PATH):
        os.remove(BACKUP_PATH)
    shutil.copy2(DB_PATH, BACKUP_PATH)
    eprint(f"[{OK}] Database backed up to {BACKUP_PATH}")


def get_session_info(cursor: sqlite3.Cursor, session_id: str) -> SessionInfo:
    """Return agent type, subagent status, and parent title for a session."""
    cursor.execute(
        "SELECT agent, parent_id FROM session WHERE id = ?",
        (session_id,),
    )
    row = cursor.fetchone()
    if not row:
        return SessionInfo(agent="unknown", is_subagent=False, parent_title=None)

    agent, parent_id = row
    is_subagent = parent_id is not None
    parent_title = None

    if is_subagent:
        cursor.execute("SELECT title FROM session WHERE id = ?", (parent_id,))
        parent_row = cursor.fetchone()
        if parent_row:
            parent_title = parent_row[0]

    return SessionInfo(
        agent=agent or "build",
        is_subagent=is_subagent,
        parent_title=parent_title,
    )


# ── queries ──────────────────────────────────────────────────────────

def find_stale_sessions(cursor: sqlite3.Cursor) -> list[StaleSession]:
    """Return all sessions whose directory no longer exists on disk."""
    cursor.execute("""
        SELECT id, title, directory, project_id, time_created, time_updated
        FROM session
        WHERE directory IS NOT NULL
        ORDER BY time_created DESC
    """)

    stale = []
    for sid, title, directory, project_id, created, updated in cursor.fetchall():
        if not os.path.isdir(directory):
            stale.append(StaleSession(
                id=sid,
                title=title,
                directory=directory,
                project_id=project_id,
                time_created=created,
                time_updated=updated,
            ))
    return stale


def build_worktree_index(cursor: sqlite3.Cursor) -> dict[str, str]:
    """Return {project_id: worktree_path} for all valid projects."""
    cursor.execute("""
        SELECT id, worktree FROM project
        WHERE worktree IS NOT NULL AND worktree != '/' AND worktree != ''
    """)
    return {
        row[0]: os.path.normpath(row[1])
        for row in cursor.fetchall()
        if os.path.isdir(row[1])
    }


def match_by_project_id(
    cursor: sqlite3.Cursor,
    stale: list[StaleSession],
) -> tuple[list[MatchResult], list[StaleSession]]:
    """
    Phase 1 — match sessions via project_id.

    session.project_id references project.id (a git root commit hash).
    Renaming a folder does NOT change the git identity, so the
    project_id stays valid after a rename. We JOIN project.worktree
    to get the current path directly.
    """
    if not stale:
        return [], []

    worktrees = build_worktree_index(cursor)

    placeholders = ",".join("?" for _ in stale)
    cursor.execute(f"""
        SELECT s.id, s.project_id, s.directory, s.title
        FROM session s
        WHERE s.id IN ({placeholders})
    """, [s.id for s in stale])

    matched: list[MatchResult] = []
    unmatched: list[StaleSession] = []

    for sid, project_id, directory, title in cursor.fetchall():
        display_title = title or "(untitled)"
        if project_id and project_id in worktrees:
            matched.append(MatchResult(
                session_id=sid,
                title=display_title,
                old_directory=directory,
                new_directory=worktrees[project_id],
            ))
        else:
            original = next(s for s in stale if s.id == sid)
            unmatched.append(original)

    return matched, unmatched


def match_by_path_heuristic(
    cursor: sqlite3.Cursor,
    stale: list[StaleSession],
) -> tuple[list[MatchResult], list[StaleSession]]:
    """
    Phase 2 — heuristic match by parent directory.

    Fallback for sessions with no valid project_id. If the stale
    directory shares the same parent as a current worktree, it's
    likely a rename candidate. This is NOT 100% reliable when
    multiple projects exist under the same parent.
    """
    worktrees = build_worktree_index(cursor)

    matched: list[MatchResult] = []
    unmatched: list[StaleSession] = []

    for session in stale:
        old_norm = os.path.normpath(session.directory)
        old_parent = os.path.dirname(old_norm)

        best_match = None
        for _proj_id, wt_norm in worktrees.items():
            if os.path.dirname(wt_norm) == old_parent:
                best_match = wt_norm
                break

        display_title = session.title or "(untitled)"
        if best_match:
            matched.append(MatchResult(
                session_id=session.id,
                title=display_title,
                old_directory=session.directory,
                new_directory=best_match,
            ))
        else:
            unmatched.append(session)

    return matched, unmatched


# ── commands ─────────────────────────────────────────────────────────

def cmd_check() -> int:
    """--check: list stale sessions and show matching suggestions."""
    if not ensure_db():
        return 1

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    stale = find_stale_sessions(cursor)
    if not stale:
        eprint(f"[{OK}] No stale sessions found (all directories exist).")
        conn.close()
        return 0

    eprint(f"[*] {len(stale)} session(s) point to a missing directory:\n")
    for i, session in enumerate(stale, 1):
        eprint(f"  {i}. {session.title or '(untitled)'}")
        eprint(f"     Session: {session.id}")
        eprint(f"     Directory: {session.directory}")
        eprint(f"     Created: {format_timestamp(session.time_created)}")
        eprint()

    # Phase 1 suggestions
    pj_matched, pj_unmatched = match_by_project_id(cursor, stale)
    if pj_matched:
        eprint(f"[Suggestion — project_id match] "
               f"{len(pj_matched)} session(s) can be auto-recovered:\n")
        for match in pj_matched:
            eprint(f"  {match.title}")
            eprint(f"    {match.old_directory}")
            eprint(f"    → {match.new_directory}  (via project_id)")
            eprint()

    # Phase 2 suggestions
    if pj_unmatched:
        ph_matched, _ = match_by_path_heuristic(cursor, pj_unmatched)
        if ph_matched:
            eprint(f"[Suggestion — path heuristic] "
                   f"{len(ph_matched)} possible match(es) — needs manual review:\n")
            for match in ph_matched:
                eprint(f"  {match.title}")
                eprint(f"    {match.old_directory}")
                eprint(f"    → {match.new_directory}  (same parent dir)")
                eprint()

    conn.close()
    return 0


def cmd_relink(dry_run: bool = False) -> int:
    """--relink: auto-fix stale sessions by project_id, then show path hints."""
    if not ensure_db():
        return 1

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    stale = find_stale_sessions(cursor)
    if not stale:
        eprint(f"[{OK}] No stale sessions to recover.")
        conn.close()
        return 0

    # Phase 1 — safe auto-recovery
    pj_matched, pj_unmatched = match_by_project_id(cursor, stale)
    applied_count = 0

    if pj_matched:
        eprint(f"[Phase 1 — project_id] {len(pj_matched)} session(s) to relink:\n")
        for match in pj_matched:
            eprint(f"  {match.title}")
            eprint(f"    {match.old_directory}")
            eprint(f"    → {match.new_directory}")
            eprint()

        if not dry_run:
            backup_db()
            for match in pj_matched:
                cursor.execute(
                    "UPDATE session SET directory = ? WHERE id = ?",
                    (match.new_directory, match.session_id),
                )
            conn.commit()
            applied_count += len(pj_matched)
            eprint(f"[{OK}] Relinked {len(pj_matched)} session(s) via project_id.\n")
        else:
            eprint(f"[Preview] Would relink {len(pj_matched)} session(s).\n")

    # Phase 2 — heuristic hints only (never auto-apply)
    if pj_unmatched:
        ph_matched, _ = match_by_path_heuristic(cursor, pj_unmatched)
        if ph_matched:
            eprint(f"[Phase 2 — path heuristic] "
                   f"{len(ph_matched)} possible match(es) — requires manual "
                   f"confirmation:\n")
            for match in ph_matched:
                eprint(f"  {match.title}")
                eprint(f"    {match.old_directory}")
                eprint(f"    → {match.new_directory}")
                eprint()
            eprint("Path heuristic is not 100% reliable. To apply, run:")
            for match in ph_matched:
                eprint(f'  python recover_sessions.py --old "{match.old_directory}" '
                       f'--new "{match.new_directory}"')
            eprint()

    if not pj_matched and not pj_unmatched:
        eprint(f"[{ERR}] No sessions could be auto-matched.")
        eprint("    Use --old / --new to specify paths manually.")

    conn.close()
    return 0


def cmd_update(old_path: str, new_path: str, dry_run: bool = False) -> int:
    """--old / --new: explicit path remap for exact directory match."""
    if not ensure_db():
        return 1

    old_norm = os.path.normpath(old_path)
    new_norm = os.path.normpath(new_path)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id, title, directory FROM session WHERE directory IS NOT NULL")
    to_update = [
        (sid, title, directory)
        for sid, title, directory in cursor.fetchall()
        if os.path.normpath(directory) == old_norm
    ]

    if not to_update:
        eprint(f"[{ERR}] No sessions found with directory: {old_path}")
        conn.close()
        return 1

    eprint(f"[*] {len(to_update)} session(s) to update:\n")
    for sid, title, _directory in to_update:
        eprint(f"  Session: {sid}")
        eprint(f"  Title:   {title or '(untitled)'}")
        eprint(f"  Old:     {old_path}")
        eprint(f"  New:     {new_path}")
        eprint()

    if not dry_run:
        backup_db()
        for sid, *_ in to_update:
            cursor.execute(
                "UPDATE session SET directory = ? WHERE id = ?",
                (new_norm, sid),
            )
        conn.commit()
        eprint(f"[{OK}] Updated {len(to_update)} session(s) → {new_norm}")
    else:
        eprint(f"[Preview] Would update {len(to_update)} session(s).")

    conn.close()
    return 0


def cmd_migrate(
    old_path: str,
    new_path: str,
    count: int = 5,
    dry_run: bool = False,
) -> int:
    """
    --migrate: migrate N latest sessions from old project to new project,
    updating both directory and project_id. Shows subagent distinction.
    """
    if not ensure_db():
        return 1

    old_norm = os.path.normpath(old_path)
    new_norm = os.path.normpath(new_path)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Find target project_id from new worktree
    cursor.execute("SELECT id FROM project WHERE worktree = ?", (new_norm,))
    target_row = cursor.fetchone()
    if not target_row:
        eprint(f"[{ERR}] No project found with worktree: {new_path}")
        conn.close()
        return 1
    target_project_id = target_row[0]

    # Find source project_id from old worktree (may not exist)
    cursor.execute("SELECT id FROM project WHERE worktree = ?", (old_norm,))
    source_row = cursor.fetchone()
    source_project_id = source_row[0] if source_row else None

    # Get latest N sessions from source directory
    cursor.execute("""
        SELECT id, title, directory, project_id, time_created
        FROM session
        WHERE directory = ?
        ORDER BY time_created DESC
        LIMIT ?
    """, (old_norm, count))

    rows = cursor.fetchall()
    if not rows:
        eprint(f"[{ERR}] No sessions found with directory: {old_path}")
        conn.close()
        return 1

    # Separate sessions that already belong to target project
    already_in_target = [r for r in rows if r[3] == target_project_id]
    fresh = [r for r in rows if r[3] != target_project_id]

    if not fresh:
        eprint(f"[{OK}] All {len(rows)} latest session(s) already belong to target project.")
        conn.close()
        return 0

    eprint(f"[*] {len(fresh)} session(s) to migrate ({old_path} → {new_path}):\n")
    for sid, title, _directory, pid, created in fresh:
        info = get_session_info(cursor, sid)
        sub_tag = " [subagent]" if info.is_subagent else ""
        parent_info = f"  parent: {info.parent_title}" if info.parent_title else ""

        eprint(f"  {format_timestamp(created)}  {sid}")
        eprint(f"  {title or '(untitled)'}{sub_tag}  (agent={info.agent})")
        if parent_info:
            eprint(parent_info)
        eprint(f"  old project_id: {pid[:20] if pid else 'NONE'}...")
        eprint(f"  new project_id: {target_project_id[:20]}...")
        eprint()

    if already_in_target:
        eprint(f"  ({len(already_in_target)} already in target project, skipped)\n")

    if not dry_run:
        for sid, *_ in fresh:
            cursor.execute(
                "UPDATE session SET directory = ?, project_id = ? WHERE id = ?",
                (new_norm, target_project_id, sid),
            )
        conn.commit()
        eprint(f"[{OK}] Migrated {len(fresh)} session(s) → {new_norm}")
    else:
        eprint(f"[Preview] Would migrate {len(fresh)} session(s).")

    conn.close()
    return 0


# ── entry ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        description="Relink opencode sessions after folder rename/move.",
        epilog=(
            "The session.project_id field references project.id "
            "(a git root commit hash), which stays stable across renames. "
            "--relink uses this to auto-recover sessions.\n"
            "  Docs: opencode-session-recovery/README.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check", action="store_true",
        help="List sessions whose directory no longer exists",
    )
    parser.add_argument(
        "--old",
        help="Old / stale project path (manual or migrate mode)",
    )
    parser.add_argument(
        "--new",
        help="New / current project path (manual or migrate mode)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview mode — do not modify database",
    )
    parser.add_argument(
        "--relink", action="store_true",
        help="Auto-match stale sessions via project_id",
    )
    parser.add_argument(
        "--migrate", action="store_true",
        help="Migrate N latest sessions from --old to --new, "
             "updating directory + project_id",
    )
    parser.add_argument(
        "--count", type=int, default=5,
        help="Number of latest sessions to migrate (default: 5)",
    )
    return parser


def main() -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not any([args.check, args.old, args.relink, args.migrate]):
        parser.print_help()
        return 1

    if args.check:
        return cmd_check()
    if args.relink:
        return cmd_relink(dry_run=args.dry_run)
    if args.migrate:
        if not args.old or not args.new:
            eprint("[Error] --migrate requires both --old and --new.")
            parser.print_help()
            return 1
        return cmd_migrate(args.old, args.new, count=args.count, dry_run=args.dry_run)
    if args.old and args.new:
        return cmd_update(args.old, args.new, dry_run=args.dry_run)

    eprint("[Error] --old and --new must be used together, "
           "or use --relink / --check / --migrate.")
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
