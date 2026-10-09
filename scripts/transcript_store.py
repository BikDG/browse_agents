#!/usr/bin/env python3
"""Append-aware, lossless transcript store.

Transcripts are append-only in normal operation, so the full history of a
session collapses to one current copy plus a list of byte offsets: the file as
it stood at any earlier checkpoint is just `current.jsonl` truncated to that
length. Storing 800 full copies of an 80 MB session costs 46 GiB and preserves
exactly the same information as 80 MB plus 800 integers.

The history that cannot be expressed as a prefix is the history that matters:
a /compact rewrite, a rollback, or a clobber SHRINKS the file or changes its
prefix. At that moment the old content is archived in full, compressed, as a
generation, and a new chain starts. Nothing is ever discarded.

    store/<sid>/current.jsonl        latest full content, directly usable
    store/<sid>/checkpoints.json     [{ts, length, md5}] -> any past state
    store/<sid>/gen/<ts>.jsonl.gz    full archive, written only on divergence
"""
import gzip
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from ask_paths import PLUGIN_DIR, DATA_DIR

STORE = DATA_DIR / "transcript-store"
CHUNK = 4 << 20
MIN_BYTES = 200


def _md5_prefix(path, length):
    """md5 of the first `length` bytes, or None if the file is shorter."""
    h, read = hashlib.md5(), 0
    with open(path, "rb") as fh:
        while read < length:
            block = fh.read(min(CHUNK, length - read))
            if not block:
                return None
            h.update(block)
            read += len(block)
    return h.hexdigest()


def _load_meta(sdir):
    f = sdir / "checkpoints.json"
    if not f.exists():
        return {"checkpoints": [], "generations": []}
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return {"checkpoints": [], "generations": []}


def _save_meta(sdir, meta):
    tmp = sdir / "checkpoints.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(sdir / "checkpoints.json")


def _archive(sdir, current, meta, reason):
    """Compress the existing chain before starting a new one."""
    gen = sdir / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    dest = gen / f"{ts}.jsonl.gz"
    with open(current, "rb") as src, gzip.open(dest, "wb", compresslevel=6) as out:
        shutil.copyfileobj(src, out, CHUNK)
    meta["generations"].append({
        "ts": time.time(), "file": dest.name, "reason": reason,
        "bytes": current.stat().st_size,
        "compressed": dest.stat().st_size,
        "checkpoints": meta["checkpoints"],
    })
    meta["checkpoints"] = []
    return dest


def update(sid, live_path):
    """Fold the live transcript into the store. Returns a short status string.

    Never raises on ordinary I/O problems: this runs from a hook and must not
    be able to disturb a session.
    """
    live = Path(live_path)
    try:
        st = live.stat()
    except OSError:
        return "no-file"
    sdir = STORE / sid
    current = sdir / "current.jsonl"
    if st.st_size < MIN_BYTES and not current.exists():
        # Brand-new or empty transcript: nothing worth storing yet. But once we
        # HAVE a copy, a sudden drop below this threshold is a catastrophic
        # truncation, which is precisely the event worth archiving — so the
        # threshold must not apply then.
        return "too-small"
    sdir.mkdir(parents=True, exist_ok=True)
    meta = _load_meta(sdir)

    if not current.exists():
        shutil.copy(live, current)
        os.chmod(current, 0o600)
        meta["checkpoints"].append({
            "ts": time.time(), "length": st.st_size,
            "md5": _md5_prefix(current, st.st_size),
        })
        meta["last_seen"] = {"size": st.st_size, "mtime": st.st_mtime}
        _save_meta(sdir, meta)
        return f"new {st.st_size}"

    have = current.stat().st_size

    # Fast path: if size AND mtime both match what we last folded in, the file
    # cannot have changed, so skip hashing entirely. Any rewrite moves mtime.
    seen = meta.get("last_seen") or {}
    if seen.get("size") == st.st_size and seen.get("mtime") == st.st_mtime:
        return "unchanged"

    def remember():
        meta["last_seen"] = {"size": live.stat().st_size,
                             "mtime": live.stat().st_mtime}

    if st.st_size == have:
        # Same size can still mean a rewrite, so verify rather than assume.
        if _md5_prefix(live, have) == _md5_prefix(current, have):
            remember(); _save_meta(sdir, meta)
            return "unchanged"
        _archive(sdir, current, meta, "same-size rewrite")
        shutil.copy(live, current)
        meta["checkpoints"].append({"ts": time.time(), "length": st.st_size,
                                    "md5": _md5_prefix(current, st.st_size)})
        remember()
        _save_meta(sdir, meta)
        return "diverged (same size)"

    if st.st_size > have and _md5_prefix(live, have) == _md5_prefix(current, have):
        # Pure append: copy only the new tail.
        added = st.st_size - have
        with open(live, "rb") as src, open(current, "ab") as dst:
            src.seek(have)
            shutil.copyfileobj(src, dst, CHUNK)
        meta["checkpoints"].append({
            "ts": time.time(), "length": st.st_size,
            "md5": _md5_prefix(current, st.st_size),
        })
        remember()
        _save_meta(sdir, meta)
        return f"+{added}"

    # Shrank, or the prefix changed: the old chain holds bytes the new one
    # does not. Archive it in full before replacing.
    reason = "shrink" if st.st_size < have else "prefix change"
    _archive(sdir, current, meta, reason)
    shutil.copy(live, current)
    os.chmod(current, 0o600)
    meta["checkpoints"].append({"ts": time.time(), "length": st.st_size,
                                "md5": _md5_prefix(current, st.st_size)})
    remember()
    _save_meta(sdir, meta)
    return f"diverged ({reason})"


def mark(sid, label):
    """Tag the newest checkpoint so an explicit save is findable later."""
    sdir = STORE / sid
    meta = _load_meta(sdir)
    if meta["checkpoints"]:
        meta["checkpoints"][-1]["label"] = label
        meta["checkpoints"][-1]["labelled_at"] = time.time()
        _save_meta(sdir, meta)
        return True
    return False


def restore(sid, length=None, out=None):
    """Reproduce a session's transcript as it stood at a checkpoint."""
    sdir = STORE / sid
    current = sdir / "current.jsonl"
    if not current.exists():
        return None
    data = current.read_bytes()
    if length is not None:
        data = data[:length]
    if out:
        Path(out).write_bytes(data)
    return data


def stats():
    total = files = sessions = gens = 0
    for sdir in STORE.glob("*/"):
        sessions += 1
        for p in sdir.rglob("*"):
            if p.is_file():
                files += 1
                total += p.stat().st_size
                if p.suffix == ".gz":
                    gens += 1
    return {"sessions": sessions, "files": files, "bytes": total,
            "generations": gens}


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "update":
        print(update(sys.argv[2], sys.argv[3]))
    else:
        print(json.dumps(stats(), indent=2))
