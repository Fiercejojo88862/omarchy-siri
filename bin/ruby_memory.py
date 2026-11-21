#!/usr/bin/env python3
"""Local memory for Ruby: SQLite notes/logs with optional Ollama embeddings.

DB: ~/.local/share/voice-assistant/memory.db
- entries(id, timestamp, tag, content, source, embedding_json)
- FTS5 index for keyword fallback
- Automatic migration from legacy ~/.local/state/voice-assistant/notes.md
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Iterable

STATE_DIR = Path.home() / ".local/state/voice-assistant"
SHARE_DIR = Path.home() / ".local/share/voice-assistant"
DB_PATH = SHARE_DIR / "memory.db"
LEGACY_NOTES = STATE_DIR / "notes.md"
OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = os.environ.get("RUBY_EMBED_MODEL", "nomic-embed-text")


def _ensure_dirs():
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SHARE_DIR.chmod(0o700)
    except OSError:
        pass


def _connect() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if conn is None:
        conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              tag TEXT DEFAULT '',
              content TEXT NOT NULL,
              source TEXT DEFAULT 'note',
              embedding_json TEXT DEFAULT '',
              embedding_model TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_tag ON entries(tag)"
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
                USING fts5(content, tag, content='entries', content_rowid='id')
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
                  INSERT INTO entries_fts(rowid, content, tag) VALUES (new.id, new.content, new.tag);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
                  INSERT INTO entries_fts(entries_fts, rowid, content, tag) VALUES('delete', old.id, old.content, old.tag);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
                  INSERT INTO entries_fts(entries_fts, rowid, content, tag) VALUES('delete', old.id, old.content, old.tag);
                  INSERT INTO entries_fts(rowid, content, tag) VALUES (new.id, new.content, new.tag);
                END;
                """
            )
        except sqlite3.Error:
            pass
        # Migrate existing DBs: add embedding_model column if missing.
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
            if "embedding_model" not in cols:
                conn.execute("ALTER TABLE entries ADD COLUMN embedding_model TEXT DEFAULT ''")
        except Exception:
            pass
        conn.commit()
    finally:
        if own:
            conn.close()


def _parse_legacy_line(line: str):
    # "- [2026-11-20 10:30] #gym chest day"
    m = re.match(r"^\s*-\s*\[(?P<ts>[^\]]+)\]\s*(?P<body>.+?)\s*$", line)
    if not m:
        return None
    ts = m.group("ts").strip()
    body = m.group("body").strip()
    tag = ""
    mt = re.match(r"^(#\w+)\s+(.*)$", body)
    if mt:
        tag, body = mt.group(1), mt.group(2).strip()
    else:
        mt = re.match(r"^(#\w+)$", body)
        if mt:
            tag, body = mt.group(1), ""
    return ts, tag, body


def migrate_legacy_notes(conn: sqlite3.Connection | None = None) -> int:
    if not LEGACY_NOTES.is_file():
        return 0
    own = conn is None
    if conn is None:
        conn = _connect()
    try:
        init_db(conn)
        cur = conn.execute("SELECT COUNT(*) AS n FROM entries WHERE source='legacy-notes'")
        if int(cur.fetchone()["n"]) > 0:
            return 0
        text = LEGACY_NOTES.read_text(encoding="utf-8", errors="replace")
        migrated = 0
        for line in text.splitlines():
            parsed = _parse_legacy_line(line)
            if not parsed:
                continue
            ts, tag, body = parsed
            if not body and not tag:
                continue
            content = body or tag
            conn.execute(
                "INSERT INTO entries(timestamp, tag, content, source, embedding_json, embedding_model) VALUES (?,?,?,?,?,?)",
                (ts, tag, content, "legacy-notes", "", ""),
            )
            migrated += 1
        conn.commit()
        return migrated
    finally:
        if own:
            conn.close()


def _ollama_embed(text: str, timeout: float = 8.0):
    if os.environ.get("VOICE_REHEARSAL") == "1" or os.environ.get("VOICE_DRYRUN") == "1":
        return None
    text = (text or "").strip()
    if not text:
        return None
    payload = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode("utf-8")
    for path in ("/api/embed", "/api/embeddings"):
        url = OLLAMA_URL.rstrip("/") + path
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if path == "/api/embed":
                embs = data.get("embeddings") or []
                if embs and isinstance(embs[0], list):
                    return [float(x) for x in embs[0]]
            emb = data.get("embedding")
            if isinstance(emb, list) and emb:
                return [float(x) for x in emb]
        except Exception:
            continue
    return None


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def add_entry(content: str, tag: str = "", timestamp: str | None = None, source: str = "note",
              embed: bool = True) -> int:
    content = (content or "").strip()
    tag = (tag or "").strip()
    if not content and not tag:
        return 0
    if not content:
        content = tag
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    emb_json = ""
    emb_model = ""
    if embed:
        emb = _ollama_embed(f"{tag} {content}".strip())
        if emb:
            try:
                emb_json = json.dumps(emb)
                emb_model = EMBED_MODEL
            except Exception:
                emb_json = ""
                emb_model = ""
    conn = _connect()
    try:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO entries(timestamp, tag, content, source, embedding_json, embedding_model) VALUES (?,?,?,?,?,?)",
            (ts, tag, content, source, emb_json, emb_model),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def clear_entries(tag: str | None = None) -> int:
    conn = _connect()
    try:
        init_db(conn)
        if tag:
            cur = conn.execute("DELETE FROM entries WHERE tag = ?", (tag,))
        else:
            cur = conn.execute("DELETE FROM entries")
        try:
            conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        except sqlite3.Error:
            pass
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def recent_entries(tag: str | None = None, limit: int = 10):
    conn = _connect()
    try:
        init_db(conn)
        migrate_legacy_notes(conn)
        if tag:
            cur = conn.execute(
                "SELECT id,timestamp,tag,content,source FROM entries WHERE tag=? ORDER BY id DESC LIMIT ?",
                (tag, limit),
            )
        else:
            cur = conn.execute(
                "SELECT id,timestamp,tag,content,source FROM entries ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _fts_search(conn: sqlite3.Connection, query: str, limit: int = 5):
    q = re.sub(r"[^\w#\s-]+", " ", (query or "").lower()).strip()
    q = re.sub(r"\s+", " ", q)
    if not q:
        return []
    # Simple OR query for robustness.
    terms = [t for t in q.split() if t]
    if not terms:
        return []
    match = " OR ".join(terms[:8])
    try:
        cur = conn.execute(
            """
            SELECT e.id,e.timestamp,e.tag,e.content,e.source, bm25(entries_fts) AS rank
            FROM entries_fts f JOIN entries e ON e.id = f.rowid
            WHERE entries_fts MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (match, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        # fallback LIKE
        like = "%" + "%".join(terms[:4]) + "%"
        cur = conn.execute(
            """
            SELECT id,timestamp,tag,content,source, 0 as rank FROM entries
            WHERE lower(content) LIKE ? OR lower(tag) LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (like, like, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def search_memory(query: str, limit: int = 5):
    conn = _connect()
    try:
        init_db(conn)
        migrate_legacy_notes(conn)
        qemb = _ollama_embed(query)
        rows = conn.execute(
            "SELECT id,timestamp,tag,content,source,embedding_json,embedding_model FROM entries ORDER BY id DESC LIMIT 500"
        ).fetchall()
        scored = []
        if qemb:
            for r in rows:
                emb_json = r["embedding_json"] or ""
                emb_model = r["embedding_model"] or ""
                # Only compare vectors produced by the current embedding model.
                if not emb_json or (emb_model and emb_model != EMBED_MODEL):
                    continue
                if not emb_model:
                    # Legacy/unknown vectors may be incompatible; skip for embedding search.
                    continue
                try:
                    emb = json.loads(emb_json)
                except Exception:
                    continue
                score = _cosine(qemb, emb)
                if score > 0.15:
                    scored.append((score, dict(r)))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            out = []
            for score, d in scored[:limit]:
                d["score"] = round(float(score), 4)
                d.pop("embedding_json", None)
                d.pop("embedding_model", None)
                out.append(d)
            if out:
                return out
        # Fallback: FTS5/keyword
        return _fts_search(conn, query, limit=limit)
    finally:
        conn.close()


def format_entries(entries, max_items: int = 5) -> str:
    lines = []
    for e in entries[:max_items]:
        ts = e.get("timestamp", "")
        tag = e.get("tag", "")
        content = e.get("content", "")
        prefix = f"[{ts}] " if ts else ""
        tagp = f"{tag} " if tag else ""
        lines.append(f"- {prefix}{tagp}{content}".strip())
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "migrate", "add", "recent", "search", "clear"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    if args.cmd == "init":
        init_db(); migrate_legacy_notes(); print("ok")
    elif args.cmd == "migrate":
        print(migrate_legacy_notes())
    elif args.cmd == "add":
        print(add_entry(args.text or "", tag=args.tag))
    elif args.cmd == "recent":
        print(format_entries(recent_entries(tag=args.tag or None, limit=args.limit), args.limit))
    elif args.cmd == "search":
        print(format_entries(search_memory(args.text or "", limit=args.limit), args.limit))
    elif args.cmd == "clear":
        print(clear_entries(tag=args.tag or None))
