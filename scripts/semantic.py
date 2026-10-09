#!/usr/bin/env python3
"""Semantic search for ask-expert: local (or Voyage) embeddings + FAISS.

Runs under the plugin's dedicated venv (data/venv) so sentence-transformers /
faiss / numpy are available. System-python scripts (find_match.py, browse_tui.py)
invoke this as a subprocess and never import these heavy deps directly.

Provider is pluggable via data/semantic/config.json:
  {"provider": "local", "model": "BAAI/bge-small-en-v1.5"}   # default, offline, free
  {"provider": "voyage", "model": "voyage-3.5-lite"}          # needs data/voyage.env key

Subcommands:
  build [--rebuild]   Embed indexed sessions (incremental by content hash) and
                      (re)build the FAISS index. Progress shown on stderr.
  query "<text>" [-k N]
                      Embed the query; print top-N as JSON on stdout:
                      [{"id": "<sid>", "score": <cos>}].
  stats               Index coverage.
  check               Load the model and embed a doc+query to validate setup.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from ask_paths import PLUGIN_DIR, DATA_DIR

SEM_DIR = DATA_DIR / "semantic"
INDEX_FILE = DATA_DIR / "index.json"
CONFIG_FILE = SEM_DIR / "config.json"
EMB_FILE = SEM_DIR / "embeddings.npy"
META_FILE = SEM_DIR / "meta.json"
FAISS_FILE = SEM_DIR / "index.faiss"
KEY_FILE = DATA_DIR / "voyage.env"

DEFAULT_PROVIDER = "local"
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_VOYAGE_MODEL = "voyage-3.5-lite"
DOC_CHAR_CAP = 6000
VOYAGE_BATCH = 64


def log(msg, end="\n"):
    sys.stderr.write(msg + end)
    sys.stderr.flush()


def load_config():
    provider, model = DEFAULT_PROVIDER, None
    if CONFIG_FILE.exists():
        try:
            c = json.loads(CONFIG_FILE.read_text())
            provider = c.get("provider", DEFAULT_PROVIDER)
            model = c.get("model")
        except (json.JSONDecodeError, OSError):
            pass
    if not model:
        model = DEFAULT_VOYAGE_MODEL if provider == "voyage" else DEFAULT_LOCAL_MODEL
    return {"provider": provider, "model": model}


# ---------------------------------------------------------------- embedders

class LocalEmbedder:
    """sentence-transformers; offline, free. BGE models get a query instruction."""

    def __init__(self, model):
        from sentence_transformers import SentenceTransformer
        self.name = model
        log(f"loading local model {model} (first run downloads it) ...")
        self.model = SentenceTransformer(model)
        self.q_instr = (
            "Represent this sentence for searching relevant passages: "
            if "bge" in model.lower() else ""
        )

    def embed_documents(self, texts):
        v = self.model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True,
            batch_size=32, show_progress_bar=True,
        )
        return [row.astype("float32") for row in v]

    def embed_query(self, text):
        v = self.model.encode(
            [self.q_instr + text], normalize_embeddings=True, convert_to_numpy=True,
        )
        return v[0].astype("float32")


class VoyageEmbedder:
    """Voyage API; needs a key (env VOYAGE_API_KEY or data/voyage.env)."""

    def __init__(self, model):
        import voyageai
        self.name = model
        key = os.environ.get("VOYAGE_API_KEY", "").strip()
        if not key and KEY_FILE.exists():
            for line in KEY_FILE.read_text().splitlines():
                if line.strip().startswith("VOYAGE_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not key:
            log("ERROR: no VOYAGE_API_KEY (env or data/voyage.env).")
            sys.exit(3)
        self.client = voyageai.Client(api_key=key)

    def embed_documents(self, texts):
        import numpy as np
        out, n = [], len(texts)
        for s in range(0, n, VOYAGE_BATCH):
            chunk = texts[s:s + VOYAGE_BATCH]
            resp = self.client.embed(chunk, model=self.name, input_type="document")
            out.extend(np.asarray(v, dtype="float32") for v in resp.embeddings)
            done = min(s + VOYAGE_BATCH, n)
            log(f"\r  embedding {done}/{n} ({int(done*100/n)}%)   ", end="")
        if n:
            log("")
        return out

    def embed_query(self, text):
        import numpy as np
        v = self.client.embed([text], model=self.name, input_type="query").embeddings[0]
        return np.asarray(v, dtype="float32")


def get_embedder(cfg):
    if cfg["provider"] == "voyage":
        return VoyageEmbedder(cfg["model"])
    return LocalEmbedder(cfg["model"])


# ---------------------------------------------------------------- core

def build_doc(meta):
    title = meta.get("title") or ""
    summary = meta.get("summary") or ""
    topics = ", ".join(meta.get("topics") or [])
    first = meta.get("first_prompt") or ""
    last = meta.get("last_prompt") or ""
    searchable = meta.get("searchable_text") or ""
    doc = (f"{title}\n{summary}\nTopics: {topics}\n"
           f"First: {first}\nLast: {last}\n{searchable}")
    return doc[:DOC_CHAR_CAP]


def doc_hash(provider, model, doc):
    key = f"{provider}\x00{model}\x00{doc}"
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def load_cache():
    import numpy as np
    cache = {}
    if META_FILE.exists() and EMB_FILE.exists():
        try:
            meta = json.loads(META_FILE.read_text())
            arr = np.load(EMB_FILE)
            for i, item in enumerate(meta.get("items", [])):
                if i < len(arr):
                    cache[item["id"]] = {"hash": item["hash"], "vec": arr[i]}
        except (json.JSONDecodeError, OSError, ValueError):
            cache = {}
    return cache


def cmd_build(rebuild=False):
    import numpy as np
    import faiss

    if not INDEX_FILE.exists():
        log("ERROR: index.json not found — run index_sessions.py first.")
        return 1
    sessions = json.loads(INDEX_FILE.read_text()).get("sessions", {})
    if not sessions:
        log("No sessions in index.json; nothing to embed.")
        return 0

    cfg = load_config()
    provider, model = cfg["provider"], cfg["model"]
    SEM_DIR.mkdir(parents=True, exist_ok=True)
    cache = {} if rebuild else load_cache()

    docs, to_ids, to_texts = {}, [], []
    for sid, meta in sessions.items():
        doc = build_doc(meta)
        h = doc_hash(provider, model, doc)
        docs[sid] = h
        if not rebuild and sid in cache and cache[sid]["hash"] == h:
            continue
        to_ids.append(sid)
        to_texts.append(doc)

    log(f"sessions: {len(sessions)}  reused: {len(sessions)-len(to_ids)}  "
        f"to embed: {len(to_ids)}  provider: {provider}  model: {model}")

    if to_ids:
        emb = get_embedder(cfg)
        vecs = emb.embed_documents(to_texts)
        for sid, v in zip(to_ids, vecs):
            cache[sid] = {"hash": docs[sid], "vec": np.asarray(v, dtype="float32")}

    ids = [s for s in sessions.keys() if s in cache]
    if not ids:
        log("No embeddings available.")
        return 1
    mat = np.vstack([cache[s]["vec"] for s in ids]).astype("float32")
    dim = mat.shape[1]
    faiss.normalize_L2(mat)
    index = faiss.IndexFlatIP(dim)
    index.add(mat)

    np.save(EMB_FILE, mat)
    META_FILE.write_text(json.dumps({
        "provider": provider, "model": model, "dim": int(dim),
        "items": [{"id": s, "hash": docs[s]} for s in ids],
    }, indent=2))
    faiss.write_index(index, str(FAISS_FILE))
    log(f"built FAISS index: {len(ids)} vectors, dim {dim} -> {FAISS_FILE}")
    return 0


def cmd_query(text, k):
    import numpy as np
    import faiss

    if not (FAISS_FILE.exists() and META_FILE.exists()):
        log("ERROR: no FAISS index yet — run: semantic.py build")
        return 1
    meta = json.loads(META_FILE.read_text())
    ids = [it["id"] for it in meta.get("items", [])]
    cfg = {"provider": meta.get("provider", load_config()["provider"]),
           "model": meta.get("model", load_config()["model"])}
    index = faiss.read_index(str(FAISS_FILE))

    emb = get_embedder(cfg)
    q = np.asarray([emb.embed_query(text)], dtype="float32")
    faiss.normalize_L2(q)
    k = min(k, len(ids))
    scores, rows = index.search(q, k)
    out = [{"id": ids[r], "score": round(float(s), 4)}
           for s, r in zip(scores[0], rows[0]) if 0 <= r < len(ids)]
    print(json.dumps(out))
    return 0


def cmd_stats():
    n_idx = len(json.loads(INDEX_FILE.read_text()).get("sessions", {})) if INDEX_FILE.exists() else 0
    cfg = load_config()
    n_vec, provider, model = 0, cfg["provider"], cfg["model"]
    if META_FILE.exists():
        m = json.loads(META_FILE.read_text())
        n_vec = len(m.get("items", []))
        provider, model = m.get("provider", provider), m.get("model", model)
    log(f"indexed sessions: {n_idx}   embedded: {n_vec}")
    log(f"provider: {provider}   model: {model}   faiss present: {FAISS_FILE.exists()}")
    return 0


def cmd_check():
    cfg = load_config()
    try:
        emb = get_embedder(cfg)
        d = emb.embed_documents(["hello world"])[0]
        _ = emb.embed_query("hi")
    except Exception as e:  # noqa: BLE001
        log(f"CHECK FAILED: {e}")
        return 1
    log(f"CHECK OK: provider={cfg['provider']} model={cfg['model']} dim={len(d)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--rebuild", action="store_true")
    q = sub.add_parser("query"); q.add_argument("text"); q.add_argument("-k", type=int, default=25)
    sub.add_parser("stats")
    sub.add_parser("check")
    args = ap.parse_args()
    if args.cmd == "build":
        return cmd_build(rebuild=args.rebuild)
    if args.cmd == "query":
        return cmd_query(args.text, args.k)
    if args.cmd == "stats":
        return cmd_stats()
    if args.cmd == "check":
        return cmd_check()
    return 2


if __name__ == "__main__":
    sys.exit(main())
