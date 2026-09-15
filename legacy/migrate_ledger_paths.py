#!/usr/bin/env python
"""Rewrite absolute artifact paths in a ledger to repository-relative ones.

Historical ledgers stored `run_dir`, `macro`, `hits_file` and `done_marker` as
absolute paths under `<repo>/parameter_optimization/`. That pinned the directory
name and made a checkout non-portable. This rewrites them to repository-relative
paths under `data/`, which `experiment_database` resolves on read.

Idempotent: a path that is already relative is left alone. Run with `--check` to
report without writing. Backups are the caller's responsibility.
"""
from __future__ import annotations
import argparse, os, sqlite3, sys

COLUMNS = (("trials", "run_dir"),
           ("sub_runs", "macro"),
           ("sub_runs", "hits_file"),
           ("sub_runs", "done_marker"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_DIR, NEW_DIR = "parameter_optimization", "data"


def to_relative(value, repo_root=REPO_ROOT):
    """Absolute repo path -> repo-relative, renaming the evidence directory."""
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        return value
    prefix = repo_root.rstrip("/") + "/"
    if not value.startswith(prefix):
        return value                      # outside the repo: leave it exactly
    rest = value[len(prefix):]
    if rest == OLD_DIR or rest.startswith(OLD_DIR + "/"):
        rest = NEW_DIR + rest[len(OLD_DIR):]
    return rest


def migrate(path, check=False, repo_root=REPO_ROOT):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    changed = {}
    try:
        for table, column in COLUMNS:
            if table not in tables:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                continue
            rows = conn.execute(
                f'SELECT rowid AS rid, "{column}" AS v FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL').fetchall()
            updates = [(to_relative(r["v"], repo_root), r["rid"])
                       for r in rows if to_relative(r["v"], repo_root) != r["v"]]
            if updates:
                changed[f"{table}.{column}"] = len(updates)
                if not check:
                    conn.executemany(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                        updates)
        if not check:
            conn.commit()
    finally:
        conn.close()
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("databases", nargs="+")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)
    total = 0
    for db in args.databases:
        if not os.path.isfile(db):
            print(f"{db}: NOT FOUND", file=sys.stderr); return 2
        changed = migrate(db, check=args.check)
        total += sum(changed.values())
        label = "would rewrite" if args.check else "rewrote"
        if changed:
            print(f"{os.path.basename(db)}:")
            for k, v in changed.items():
                print(f"    {label} {v:>7} in {k}")
        else:
            print(f"{os.path.basename(db)}: already relative")
    print(f"total {'pending' if args.check else 'rewritten'}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
