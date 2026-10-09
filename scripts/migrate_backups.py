#!/usr/bin/env python3
"""Seed the append-aware store from the existing transcript-backups pile.

For each session the newest copy becomes current.jsonl, and every older copy
contributes a checkpoint {ts, length}. Because each older copy was verified to
be a byte-exact prefix of the newest, truncating current.jsonl to a checkpoint
length reproduces that copy exactly. The history survives the quarantine as
offsets rather than as 78 GiB of duplicated prefixes.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BACKUP_DIR = DATA_DIR / "transcript-backups"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript_store as store

STAMP = re.compile(r"\.(\d{8}T\d{6})\.jsonl$")


def main():
    apply = "--apply" in sys.argv
    per = defaultdict(list)
    for p in BACKUP_DIR.glob("*.jsonl"):
        m = STAMP.search(p.name)
        if m:
            per[p.name.split(".")[0]].append((m.group(1), p.stat().st_size, p))
    for v in per.values():
        v.sort()

    n_sess = n_ckpt = n_bytes = 0
    for sid, entries in sorted(per.items()):
        newest = entries[-1]
        n_sess += 1
        n_ckpt += len(entries)
        n_bytes += newest[1]
        if not apply:
            continue
        sdir = store.STORE / sid
        sdir.mkdir(parents=True, exist_ok=True)
        current = sdir / "current.jsonl"
        if not current.exists():
            shutil.copy(newest[2], current)
            os.chmod(current, 0o600)
        meta = store._load_meta(sdir)
        if meta["checkpoints"]:
            continue                      # already seeded
        for ts, size, _p in entries:
            when = time.mktime(time.strptime(ts, "%Y%m%dT%H%M%S"))
            meta["checkpoints"].append({
                "ts": when, "length": size, "md5": None,
                "from": "migrated",
            })
        st = current.stat()
        meta["last_seen"] = {"size": st.st_size, "mtime": st.st_mtime}
        store._save_meta(sdir, meta)

    print(f"sessions            : {n_sess}")
    print(f"checkpoints recorded: {n_ckpt}")
    print(f"current.jsonl bytes : {n_bytes / 2**30:.2f} GiB")
    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
