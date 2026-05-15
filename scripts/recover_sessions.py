#!/usr/bin/env python3
"""
opencode-session-recovery — Relink orphaned sessions after folder rename/move.

Recovers opencode conversation history that becomes inaccessible when a
project folder is renamed or moved. Uses the session ↔ project relationship
in opencode's SQLite database to relink stale directory paths.

For opencode Skill usage, see ../SKILL.md.
For standalone usage: python recover_sessions.py --help
"""

import sqlite3
import os
import shutil
import argparse
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db')
BACKUP_PATH = DB_PATH + '.session-recovery-backup'

# ── helpers ──────────────────────────────────────────────────────────

OK = '\u2713'   # ✓
ERR = '\u2717'  # ✗


def eprint(*args, **kwargs):
    """Print to stderr, safe for piped output."""
    import sys
    print(*args, file=sys.stderr, **kwargs)


def backup_db():
    shutil.copy2(DB_PATH, BACKUP_PATH)
    eprint(f'[{OK}] Database backed up to {BACKUP_PATH}')


def ensure_db():
    """Return True if database exists and is readable."""
    p = Path(DB_PATH)
    if not p.exists():
        eprint(f'[{ERR}] Database not found: {DB_PATH}')
        eprint('    Expected at ~/.local/share/opencode/opencode.db')
        return False
    if not os.access(str(p), os.R_OK):
        eprint(f'[{ERR}] No read permission: {DB_PATH}')
        return False
    return True


# ── queries ──────────────────────────────────────────────────────────

def find_stale(cursor):
    """Return sessions whose directory no longer exists on disk."""
    cursor.execute('''
        SELECT id, title, directory, time_created, time_updated
        FROM session
        WHERE directory IS NOT NULL
        ORDER BY time_created DESC
    ''')
    stale = []
    for sid, title, directory, created, updated in cursor.fetchall():
        if not os.path.isdir(directory):
            stale.append((sid, title, directory, created, updated))
    return stale


def existing_worktrees(cursor):
    """Return {project_id: worktree_path} for all non-virtual projects."""
    cursor.execute('''
        SELECT id, worktree FROM project
        WHERE worktree IS NOT NULL AND worktree != '/' AND worktree != ''
    ''')
    return {row[0]: os.path.normpath(row[1])
            for row in cursor.fetchall()
            if os.path.isdir(row[1])}


def match_by_project(cursor, stale):
    """
    Phase 1 — reliable match via project_id.

    session.project_id references project.id (a git HEAD hash).
    Renaming a folder does NOT change the git identity, so the
    project_id stays valid after a rename. We JOIN project.worktree
    to get the current path directly.
    """
    wt_index = existing_worktrees(cursor)

    if not stale:
        return [], []

    placeholders = ','.join('?' for _ in stale)
    cursor.execute(f'''
        SELECT s.id, s.project_id, s.directory, s.title
        FROM session s
        WHERE s.id IN ({placeholders})
    ''', [sid for sid, *_ in stale])

    matched, unmatched = [], []
    for sid, proj_id, directory, title in cursor.fetchall():
        label = title or '(untitled)'
        if proj_id and proj_id in wt_index:
            matched.append((sid, label, directory, wt_index[proj_id]))
        else:
            unmatched.append((sid, label, directory, proj_id))

    return matched, unmatched


def match_by_path(cursor, stale):
    """
    Phase 2 — heuristic match by parent directory.

    Fallback for sessions with no valid project_id. If the stale
    directory shares the same parent as a current worktree, it's
    likely a rename candidate. This is NOT 100% reliable when
    multiple projects exist under the same parent.
    """
    wt_index = existing_worktrees(cursor)

    matched, unmatched = [], []
    for sid, title, directory, *rest in stale:
        old_norm = os.path.normpath(directory)
        old_parent = os.path.dirname(old_norm)

        best = None
        for proj_id, wt_norm in wt_index.items():
            if os.path.dirname(wt_norm) == old_parent:
                best = wt_norm
                break

        label = title or '(untitled)'
        if best:
            matched.append((sid, label, directory, best))
        else:
            unmatched.append((sid, label, directory, None))

    return matched, unmatched


# ── commands ─────────────────────────────────────────────────────────

def cmd_check():
    """--check: list stale sessions and show matching suggestions."""
    if not ensure_db():
        return 1

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    stale = find_stale(c)
    if not stale:
        eprint(f'[{OK}] No stale sessions found (all directories exist).')
        conn.close()
        return 0

    eprint(f'[*] {len(stale)} session(s) point to a missing directory:\n')
    for i, (sid, title, directory, created, updated) in enumerate(stale, 1):
        ts = (datetime.fromtimestamp(created / 1000).strftime('%Y-%m-%d %H:%M:%S')
              if created else 'N/A')
        eprint(f'  {i}. {title or "(untitled)"}')
        eprint(f'     Session: {sid}')
        eprint(f'     Directory: {directory}')
        eprint(f'     Created: {ts}')
        eprint()

    # Phase 1 suggestions
    pj_ok, pj_fail = match_by_project(c, stale)
    if pj_ok:
        eprint(f'[Suggestion — project_id match] '
               f'{len(pj_ok)} session(s) can be auto-recovered:\n')
        for sid, title, old_dir, new_dir in pj_ok:
            eprint(f'  {title}')
            eprint(f'    {old_dir}')
            eprint(f'    → {new_dir}  (via project_id)')
            eprint()

    # Phase 2 suggestions
    failed_ids = {sid for sid, *_ in pj_fail}
    path_candidates = [s for s in stale if s[0] in failed_ids]
    if path_candidates:
        ph_ok, _ = match_by_path(c, path_candidates)
        if ph_ok:
            eprint(f'[Suggestion — path heuristic] '
                   f'{len(ph_ok)} possible match(es) — needs manual review:\n')
            for sid, title, old_dir, new_dir in ph_ok:
                eprint(f'  {title}')
                eprint(f'    {old_dir}')
                eprint(f'    → {new_dir}  (same parent dir)')
                eprint()

    conn.close()
    return 0


def cmd_relink(dry_run=False):
    """--relink: auto-fix stale sessions by project_id, then show path hints."""
    if not ensure_db():
        return 1

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    stale = find_stale(c)
    if not stale:
        eprint(f'[{OK}] No stale sessions to recover.')
        conn.close()
        return 0

    applied = 0

    # Phase 1 — safe auto-recovery
    pj_ok, pj_fail = match_by_project(c, stale)
    if pj_ok:
        eprint(f'[Phase 1 — project_id] {len(pj_ok)} session(s) to relink:\n')
        for sid, title, old_dir, new_dir in pj_ok:
            eprint(f'  {title}')
            eprint(f'    {old_dir}')
            eprint(f'    → {new_dir}')
            eprint()

        if not dry_run:
            if not os.path.exists(BACKUP_PATH):
                backup_db()
            for sid, title, old_dir, new_dir in pj_ok:
                c.execute('UPDATE session SET directory = ? WHERE id = ?',
                          (new_dir, sid))
            conn.commit()
            applied += len(pj_ok)
            eprint(f'[{OK}] Relinked {len(pj_ok)} session(s) via project_id.\n')
        else:
            eprint(f'[Preview] Would relink {len(pj_ok)} session(s).\n')

    # Phase 2 — heuristic hints only
    failed_ids = {sid for sid, *_ in pj_fail}
    path_candidates = [s for s in stale if s[0] in failed_ids]
    if path_candidates:
        ph_ok, _ = match_by_path(c, path_candidates)
        if ph_ok:
            eprint(f'[Phase 2 — path heuristic] '
                   f'{len(ph_ok)} possible match(es) — requires manual '
                   f'confirmation:\n')
            for sid, title, old_dir, new_dir in ph_ok:
                eprint(f'  {title}')
                eprint(f'    {old_dir}')
                eprint(f'    → {new_dir}')
                eprint()
            eprint('Path heuristic is not 100% reliable. To apply, run:')
            for sid, title, old_dir, new_dir in ph_ok:
                eprint(f'  python recover_sessions.py --old "{old_dir}" '
                       f'--new "{new_dir}"')
            eprint()

    if not pj_ok and not path_candidates:
        eprint(f'[{ERR}] No sessions could be auto-matched.')
        eprint('    Use --old / --new to specify paths manually.')

    conn.close()
    return 0


def cmd_update(old_path, new_path, dry_run=False):
    """--old / --new: explicit path remap for exact directory match."""
    if not ensure_db():
        return 1

    old_norm = os.path.normpath(old_path)
    new_norm = os.path.normpath(new_path)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('SELECT id, title, directory FROM session WHERE directory IS NOT NULL')
    to_update = [(sid, title, directory)
                 for sid, title, directory in c.fetchall()
                 if os.path.normpath(directory) == old_norm]

    if not to_update:
        eprint(f'[{ERR}] No sessions found with directory: {old_path}')
        conn.close()
        return 1

    eprint(f'[*] {len(to_update)} session(s) to update:\n')
    for sid, title, directory in to_update:
        eprint(f'  Session: {sid}')
        eprint(f'  Title:   {title or "(untitled)"}')
        eprint(f'  Old:     {directory}')
        eprint(f'  New:     {new_path}')
        eprint()

    if not dry_run:
        if not os.path.exists(BACKUP_PATH):
            backup_db()
        for sid, *_ in to_update:
            c.execute('UPDATE session SET directory = ? WHERE id = ?',
                      (new_norm, sid))
        conn.commit()
        eprint(f'[{OK}] Updated {len(to_update)} session(s) → {new_norm}')
    else:
        eprint(f'[Preview] Would update {len(to_update)} session(s).')

    conn.close()
    return 0


# ── entry ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Relink opencode sessions after folder rename/move.',
        epilog=(
            'The session.project_id field references project.id '
            '(a git HEAD hash), which stays stable across renames. '
            '--relink uses this to auto-recover sessions.\n'
            '  Docs: opencode-session-recovery/README.md'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--check', action='store_true',
                        help='List sessions whose directory no longer exists')
    parser.add_argument('--old',
                        help='Old / stale project path (manual mode)')
    parser.add_argument('--new',
                        help='New / current project path (manual mode)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview mode — do not modify database')
    parser.add_argument('--relink', action='store_true',
                        help='Auto-match stale sessions via project_id')
    args = parser.parse_args()

    if not any([args.check, args.old, args.relink]):
        parser.print_help()
        return 1

    if args.check:
        return cmd_check()
    elif args.relink:
        return cmd_relink(dry_run=args.dry_run)
    elif args.old and args.new:
        return cmd_update(args.old, args.new, dry_run=args.dry_run)
    else:
        eprint('[Error] --old and --new must be used together, '
               'or use --relink / --check.')
        parser.print_help()
        return 1


if __name__ == '__main__':
    exit(main())
