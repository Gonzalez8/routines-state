"""Shared helpers for the routines-state scripts.

Keep this module dependency-free so every script can be called from a
Claude Code Routine without extra setup (pure stdlib only).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
SENT_NEWS_PATH = STATE_DIR / "sent_news.json"
CONFIG_PATH = STATE_DIR / "config.json"

# Tracking query/fragment params pollute canonical URLs and break dedupe.
_TRACKING_PREFIXES = ("utm_", "mc_", "hsa_", "fb_", "gclid", "ref_", "ref=", "spm")
_TRACKING_EXACT = {"gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "ref"}


def utcnow_iso() -> str:
    """Return an ISO-8601 UTC timestamp with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    """Load a JSON file, returning ``default`` if the file is missing."""
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically so a crash mid-write cannot corrupt state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        # Best effort: remove the temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_config() -> dict:
    """Load config.json with sane fallback defaults."""
    defaults = {
        "retention_days": 30,
        "max_items": 2000,
        "similarity_threshold": 0.85,
        "topics": [],
        "schema_version": 1,
    }
    cfg = load_json(CONFIG_PATH, {})
    defaults.update(cfg or {})
    return defaults


def load_state() -> dict:
    """Load sent_news.json with the expected shape."""
    return load_json(
        SENT_NEWS_PATH,
        {"schema_version": 1, "updated_at": None, "items": []},
    )


def save_state(state: dict) -> None:
    state["updated_at"] = utcnow_iso()
    atomic_write_json(SENT_NEWS_PATH, state)


# ---------------------------------------------------------------------------
# URL + title normalization
# ---------------------------------------------------------------------------

def canonicalize_url(url: str) -> str:
    """Return a canonical URL: lower-case host, no tracking params, no fragment.

    We keep the path and any remaining query intact so two legitimately
    different articles on the same domain don't collide.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Drop tracking params; keep everything else in original order.
    kept_pairs = []
    if parsed.query:
        for pair in parsed.query.split("&"):
            if not pair:
                continue
            key = pair.split("=", 1)[0].lower()
            if key in _TRACKING_EXACT or any(
                key.startswith(prefix) for prefix in _TRACKING_PREFIXES
            ):
                continue
            kept_pairs.append(pair)
    query = "&".join(kept_pairs)

    # Strip trailing slash from path (but keep "/" root).
    path = parsed.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, "", query, ""))


def source_domain(url: str) -> str:
    """Extract the registrable-ish domain from a URL, minus the ``www.`` prefix."""
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on",
    "at", "by", "with", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those",
    "new", "now", "today", "breaking",
}


def normalize_title(title: str) -> str:
    """Lower-case, strip accents/punctuation, drop stopwords.

    The goal is to collapse near-duplicate headlines from different outlets
    into the same fingerprint (e.g. "Anthropic launches Claude 4" vs.
    "Anthropic Launches New Claude 4 Model Today").
    """
    if not title:
        return ""
    # Unicode-normalize and strip accents (NFKD -> drop combining marks).
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_ish = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_ish.lower()
    cleaned = _PUNCT_RE.sub(" ", lowered)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    tokens = [t for t in cleaned.split(" ") if t and t not in _STOPWORDS]
    return " ".join(tokens)


def title_tokens(title: str) -> set[str]:
    """Return the set of content tokens used for similarity comparisons."""
    normalized = normalize_title(title)
    return set(normalized.split()) if normalized else set()


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def event_id(title: str, url: str) -> str:
    """Stable id for a news event.

    Prefer the canonical URL (strongest signal). Fall back to the normalized
    title so items without a URL still get a stable id. The resulting id is
    short enough to read at a glance but wide enough to avoid collisions.
    """
    canon = canonicalize_url(url)
    key = canon or normalize_title(title)
    if not key:
        # Last-resort: hash the raw title so we never emit a blank id.
        key = (title or "").strip()
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return digest[:16]


# ---------------------------------------------------------------------------
# Item shape helpers
# ---------------------------------------------------------------------------

REQUIRED_ITEM_FIELDS = (
    "event_id",
    "title",
    "canonical_url",
    "source_domain",
    "first_sent_at",
    "last_seen_at",
    "topic",
)


def build_item(
    *,
    title: str,
    url: str,
    topic: str,
    first_sent_at: str | None = None,
    last_seen_at: str | None = None,
) -> dict:
    """Construct a minimal state item. Drops any extra fields by design."""
    now = utcnow_iso()
    canon = canonicalize_url(url)
    return {
        "event_id": event_id(title, url),
        "title": (title or "").strip(),
        "canonical_url": canon,
        "source_domain": source_domain(url),
        "first_sent_at": first_sent_at or now,
        "last_seen_at": last_seen_at or now,
        "topic": topic or "",
    }


def prune_unknown_fields(item: dict) -> dict:
    """Keep only the allowed fields — prevents accidental state bloat."""
    return {k: item.get(k) for k in REQUIRED_ITEM_FIELDS}
