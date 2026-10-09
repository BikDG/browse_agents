#!/usr/bin/env python3
"""Quarantine transcript backups that are provably redundant.

Transcripts are append-only, so an older backup is almost always a byte-exact
PREFIX of a newer one and carries no unique information. This finds those and
MOVES them to a quarantine directory. It never deletes, and it never moves a
file it has not first verified byte-for-byte against the copy being retained.

A copy is RETAINED when it holds bytes no newer retained copy has, which is
what happens after a shrink (a /compact rewrite, a rollback, a clobber). Those
are the copies that actually protect you, so they always stay.

  --apply   perform the moves (default is a dry run)
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
from ask_paths import PLUGIN_DIR, DATA_DIR

BACKUP_DIR = DATA_DIR / "transcript-backups"
QUARANTINE = DATA_DIR / "transcript-backups-quarantine"
MANIFEST = DATA_DIR / "quarantine-manifest.json"
CHUNK = 4 << 20
STAMP = re.compile(r"\.(\d{8}T\d{6})\.jsonl$")


def copies_by_session():
    per = defaultdict(list)
    for p in BACKUP_DIR.glob("*.jsonl"):
        m = STAMP.search(p.name)
        if not m:
            continue                      # unexpected name: never touch it
        try:
            per[p.name.split(".")[0]].append((m.group(1), p.stat().st_size, p))
        except OSError:
            pass
    for v in per.values():
        v.sort()                          # timestamp ascending
    return per


def retained_indices(entries):
    """Indices holding bytes no newer retained copy has."""
    keep = {len(entries) - 1}
    floor = entries[-1][1]
    for i in range(len(entries) - 2, -1, -1):
        if entries[i][1] > floor:
            keep.add(i)
            floor = entries[i][1]
    return keep


def prefix_digests(path, sizes):
    """md5 of path's first N bytes, for each N in sizes, in one pass."""
    want = sorted(set(sizes))
    out, h, read, i = {}, hashlib.md5(), 0, 0
    with open(path, "rb") as fh:
        while i < len(want):
            target = want[i]
            if read >= target:
                out[target] = h.hexdigest() if read == target else None
                i += 1
                continue
            block = fh.read(min(CHUNK, target - read))
            if not block:
                break
            h.update(block)
            read += len(block)
            if read == target:
                out[target] = h.hexdigest()
                i += 1
    return out


def digest(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def main():
    apply = "--apply" in sys.argv
    per = copies_by_session()
    moves, kept, failed = [], 0, []
    freed = 0
    t0 = time.time()
    for n, (sid, entries) in enumerate(sorted(per.items()), 1):
        keep_idx = retained_indices(entries)
        kept += len(keep_idx)
        cands = [(i, e) for i, e in enumerate(entries) if i not in keep_idx]
        if not cands:
            continue
        # Verify each candidate against the oldest retained copy that is at
        # least as large, which is the one that should contain it.
        for i, (ts, size, path) in cands:
            target = None
            for j in sorted(keep_idx):
                if entries[j][1] >= size and j > i:
                    target = entries[j]
                    break
            if target is None:
                failed.append((str(path), "no retained copy large enough"))
                continue
            want = prefix_digests(target[2], [size]).get(size)
            got = digest(path)
            if want is None or want != got:
                failed.append((str(path), f"prefix mismatch vs {target[2].name}"))
                continue
            moves.append({"from": str(path), "retained": str(target[2]),
                          "bytes": size, "md5": got})
            freed += size
        if n % 40 == 0:
            print(f"  ...{n}/{len(per)} sessions, {len(moves)} verified redundant",
                  file=sys.stderr, flush=True)

    print(f"\nsessions              : {len(per)}")
    print(f"copies retained       : {kept}")
    print(f"verified redundant    : {len(moves)}  ({freed / 2**30:.2f} GiB)")
    print(f"failed verification   : {len(failed)}  (these stay put)")
    for p, why in failed[:10]:
        print(f"   ! {Path(p).name}: {why}")
    print(f"scan took {time.time() - t0:.0f}s")

    if not apply:
        print("\nDRY RUN — nothing moved. Re-run with --apply.")
        return 0

    QUARANTINE.mkdir(parents=True, exist_ok=True)
    moved = 0
    for rec in moves:
        src = Path(rec["from"])
        try:
            shutil.move(str(src), str(QUARANTINE / src.name))
            moved += 1
        except OSError as e:
            rec["error"] = str(e)
    MANIFEST.write_text(json.dumps(
        {"created": time.time(), "moved": moved, "bytes": freed,
         "quarantine": str(QUARANTINE), "failed": failed, "moves": moves},
        indent=2))
    print(f"\nmoved {moved} files to {QUARANTINE}")
    print(f"manifest: {MANIFEST}")
    print("Nothing was deleted. Review, then remove the quarantine directory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
