#!/usr/bin/env python3
"""Given a session id, print the canonical project cwd to cd into before
`claude --resume <id>`. The project dir name (e.g. `-home-bik-workspace-haven`)
is ambiguous when path components contain `-`, so we brute-force every
partition and pick the one that exists on disk.
"""
import os
import sys
from pathlib import Path


def decode(name):
    parts = name.lstrip("-").split("-")
    n = len(parts)
    if n == 0:
        return None
    best = None
    for mask in range(1 << (n - 1)):
        s = parts[0]
        for i in range(n - 1):
            s += "/" if (mask >> i) & 1 else "-"
            s += parts[i + 1]
        path = "/" + s
        if os.path.isdir(path):
            depth = path.count("/")
            if best is None or depth > best[0]:
                best = (depth, path)
    return best[1] if best else None


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    sid = sys.argv[1]
    for p in Path.home().glob(f".claude/projects/*/{sid}.jsonl"):
        decoded = decode(p.parent.name)
        if decoded:
            print(decoded)
            return
    sys.exit(1)


if __name__ == "__main__":
    main()
