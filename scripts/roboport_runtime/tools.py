"""Tool registry and implementations for the local Ollama runtime.

Per-agent tool whitelists come from config/agent_config.yaml. The executor
loads only the tools the current agent is allowed to call.

Tools fall into three buckets:
  REAL    — actually does the thing (file I/O, HTTP, deterministic logic).
  STATIC  — answers from a small built-in lookup table; good enough for
            grounded reasoning, not a substitute for a real data source.
  SAMPLE  — returns illustrative seed data so the pipeline can run
            end-to-end. Each result is tagged "_stub": true. Replace with
            a real data source for production.

The shape of every tool's return value is JSON-serializable.
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests

REPO = Path(__file__).resolve().parent.parent.parent


# ----- REAL --------------------------------------------------------------

def load_profile(path: str) -> dict[str, Any]:
    """Read a profile JSON from anywhere under the repo (relative paths only)."""
    p = (REPO / path).resolve()
    if REPO.resolve() not in p.parents and p != REPO.resolve():
        return {"error": "path escapes repo root"}
    if not p.exists():
        return {"error": f"not found: {path}"}
    return json.loads(p.read_text(encoding="utf-8"))


def fetch_url(url: str, max_chars: int = 8000) -> dict[str, Any]:
    """GET a URL and return cleaned text. Strips tags; clamps length."""
    try:
        r = requests.get(url, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (ROBOPORT)"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)}
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return {"url": url, "status": r.status_code, "text": text[:max_chars],
            "truncated": len(text) > max_chars}


def dedupe_jobs(jobs: Any) -> list[dict]:
    """Remove duplicates by (title, company, location).

    Accepts either a flat list of jobs or a search-tool response object
    (`{"results": [...]}`) — agents tend to forward the whole response.
    """
    if isinstance(jobs, dict):
        jobs = jobs.get("results") or jobs.get("jobs") or []
    if not isinstance(jobs, list):
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        key = (
            (j.get("title") or "").strip().lower(),
            (j.get("company") or "").strip().lower(),
            (j.get("location") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def _extract_urls(maybe: Any) -> list[str]:
    """Pull URL strings out of a polymorphic input.

    Accepts:
      - flat list of URL strings
      - list of dicts with a 'source_url' or 'url' key (e.g. Job objects)
      - dict shaped like a search response: {"results": [...]} / {"jobs": [...]}
      - dict with a 'urls' key
    """
    if isinstance(maybe, dict):
        maybe = maybe.get("results") or maybe.get("jobs") or maybe.get("urls") or []
    if not isinstance(maybe, list):
        return []
    out: list[str] = []
    for item in maybe:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            url = item.get("source_url") or item.get("url")
            if isinstance(url, str):
                out.append(url)
    return out


def validate_url_active(urls: Any, timeout: int = 5,
                        max_workers: int = 10) -> dict[str, Any]:
    """HEAD-check URLs (or extract them from Job objects) to drop dead links.

    Live = HTTP 2xx or 3xx after following redirects.
    Dead = 4xx/5xx, timeout, or any network error.

    Falls back from HEAD to GET on 405 Method Not Allowed (some ATSs reject
    HEAD). Closes the GET response immediately to avoid downloading bodies.

    Accepts polymorphic input — see _extract_urls.

    Returns:
      {
        "checked": int,
        "live":    int,
        "dead":    int,
        "results": [{"url": str, "status": int|None, "live": bool, "error"?: str}, ...],
      }
    """
    extracted = _extract_urls(urls)
    if not extracted:
        return {"checked": 0, "live": 0, "dead": 0, "results": []}

    headers = {"User-Agent": "Mozilla/5.0 (ROBOPORT)"}

    def check_one(url: str) -> dict[str, Any]:
        if not isinstance(url, str) or not url:
            return {"url": url, "status": None, "live": False, "error": "empty or non-string url"}
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
            if r.status_code == 405:
                # Some servers (e.g. certain Workday tenants) reject HEAD.
                r = requests.get(url, timeout=timeout, allow_redirects=True,
                                 stream=True, headers=headers)
                r.close()
            return {"url": url, "status": r.status_code,
                    "live": 200 <= r.status_code < 400}
        except requests.exceptions.Timeout:
            return {"url": url, "status": None, "live": False, "error": "timeout"}
        except Exception as e:  # noqa: BLE001
            return {"url": url, "status": None, "live": False, "error": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(check_one, extracted))

    live = sum(1 for r in results if r["live"])
    return {"checked": len(results), "live": live, "dead": len(results) - live,
            "results": results}


# ----- REAL: per-board JD fetcher ---------------------------------------

# Pattern: <provider>-<slug>-<id>; matches IDs we emit from _gh_normalize / _lv_normalize.
_JOB_ID_PATTERN = re.compile(r"^(gh|lv)-([a-z0-9_.\-]+)-(.+)$", re.I)

# Greenhouse: https://boards.greenhouse.io/<slug>/jobs/<id> (also job-boards.greenhouse.io).
_GH_URL_PATTERN = re.compile(
    r"https?://(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:embed/job_app\?for=|boards/)?([a-z0-9_.\-]+)(?:/jobs)?(?:/|\?token=|\?gh_jid=)([0-9]+)",
    re.I,
)
# Lever: https://jobs.lever.co/<slug>/<job_id> (job_id is a UUID-style string).
_LV_URL_PATTERN = re.compile(
    r"https?://jobs\.lever\.co/([a-z0-9_.\-]+)/([a-z0-9\-]+)",
    re.I,
)


def _detect_jd_route(maybe: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve input to (provider, slug, job_id, url).

    Tries, in order:
      1. Job dict's `id` field if it follows the gh-/lv- pattern we emit.
      2. Job dict's `source` + parsed source_url.
      3. URL-only input — match against Greenhouse / Lever patterns.

    Returns (None, None, None, url) for unknown sources — caller should fall
    back to fetch_url.
    """
    url = None
    if isinstance(maybe, dict):
        url = maybe.get("source_url") or maybe.get("url")
        # (1) try the canonical id field first
        m = _JOB_ID_PATTERN.match((maybe.get("id") or "").strip())
        if m:
            prefix, slug, jid = m.group(1).lower(), m.group(2), m.group(3)
            provider = "greenhouse" if prefix == "gh" else "lever"
            return provider, slug, jid, url
    elif isinstance(maybe, str):
        url = maybe

    if not url:
        return None, None, None, None

    m = _GH_URL_PATTERN.search(url)
    if m:
        return "greenhouse", m.group(1), m.group(2), url
    m = _LV_URL_PATTERN.search(url)
    if m:
        return "lever", m.group(1), m.group(2), url
    return None, None, None, url


def _strip_html_to_text(s: str) -> str:
    """Convert Greenhouse-style escaped HTML to plain text."""
    if not s:
        return ""
    # Greenhouse `content` is sometimes double-escaped (HTML entities for tags).
    s = html.unescape(s)
    s = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", s, flags=re.I)
    # Convert block-level closers to newlines so we don't run paragraphs together.
    s = re.sub(r"</(p|div|li|h[1-6]|br)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    # Collapse runs of whitespace but keep paragraph breaks.
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _fetch_jd_greenhouse(slug: str, job_id: str, timeout: int = 15) -> dict[str, Any]:
    """Fetch a single Greenhouse job's full content via the public API."""
    api_url = f"{GREENHOUSE_BASE}/{slug}/jobs/{job_id}"
    try:
        r = requests.get(api_url, timeout=timeout,
                         headers={"User-Agent": "ROBOPORT/0.1"})
        if r.status_code != 200:
            return {"_error": f"greenhouse {r.status_code} on {api_url}"}
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return {"_error": f"greenhouse fetch failed: {e}"}

    return {
        "source": "greenhouse",
        "title": d.get("title", ""),
        "company": slug,
        "location": (d.get("location") or {}).get("name", "") or "",
        "body": _strip_html_to_text(d.get("content", "")),
        "structured_lists": [],  # Greenhouse doesn't structure these.
        "departments": [(dep or {}).get("name", "") for dep in (d.get("departments") or [])],
        "posted_at": (d.get("updated_at") or "")[:10],
        "api_url": api_url,
        "absolute_url": d.get("absolute_url", ""),
    }


def _fetch_jd_lever(slug: str, job_id: str, timeout: int = 15) -> dict[str, Any]:
    """Fetch a single Lever posting's full content via the public API."""
    api_url = f"{LEVER_BASE}/{slug}/{job_id}"
    try:
        r = requests.get(api_url, timeout=timeout,
                         headers={"User-Agent": "ROBOPORT/0.1"})
        if r.status_code != 200:
            return {"_error": f"lever {r.status_code} on {api_url}"}
        d = r.json()
    except Exception as e:  # noqa: BLE001
        return {"_error": f"lever fetch failed: {e}"}

    cats = d.get("categories") or {}
    # Lever ships both description (HTML) and descriptionPlain. Prefer plain;
    # fall back to stripping HTML if plain is empty (older postings).
    desc_plain = d.get("descriptionPlain") or _strip_html_to_text(d.get("description", ""))
    additional_plain = d.get("additionalPlain") or _strip_html_to_text(d.get("additional", ""))

    structured: list[dict] = []
    for item in (d.get("lists") or []):
        if not isinstance(item, dict):
            continue
        label = item.get("text", "")
        # `content` is an HTML <ul>...</ul>; we split into items.
        items_html = item.get("content", "") or ""
        items = [_strip_html_to_text(piece)
                 for piece in re.split(r"</li\s*>", items_html, flags=re.I)
                 if piece.strip()]
        items = [it for it in items if it]
        structured.append({"label": label, "items": items})

    body_parts = [desc_plain]
    for s in structured:
        if s["items"]:
            body_parts.append(s["label"])
            body_parts.extend(f"- {it}" for it in s["items"])
    if additional_plain:
        body_parts.append(additional_plain)
    body = "\n\n".join(p for p in body_parts if p).strip()

    from datetime import datetime, timezone
    posted_at = ""
    ts = d.get("createdAt")
    if isinstance(ts, (int, float)):
        posted_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()

    return {
        "source": "lever",
        "title": d.get("text", ""),
        "company": slug,
        "location": cats.get("location", "") or "",
        "body": body,
        "structured_lists": structured,
        "departments": [cats.get("department", "")] if cats.get("department") else [],
        "posted_at": posted_at,
        "api_url": api_url,
        "absolute_url": d.get("hostedUrl", ""),
    }


def fetch_jd_full(job: Any, fallback_max_chars: int = 12000) -> dict[str, Any]:
    """Fetch the full JD body for a single job.

    Routing:
      - Job dict with a `gh-<slug>-<id>` or `lv-<slug>-<id>` id: hit that
        provider's single-job JSON API for a structured body.
      - URL string matching Greenhouse / Lever patterns: same as above.
      - Anything else: fall back to fetch_url() and strip HTML.

    Greenhouse and Lever responses are far richer than scraping; in particular
    Lever exposes `lists` (Requirements, Responsibilities, etc.) which we
    flatten back into the body for the model to read, and also surface as
    `structured_lists` so downstream agents (technical_analyst, compliance_risk)
    can target specific sections.

    Returns:
      {
        "url":             str | None,
        "source":          "greenhouse" | "lever" | "fetch_url" | "error",
        "title":           str,
        "company":         str,
        "location":        str,
        "body":            str,           # plain text, paragraph-broken
        "body_chars":      int,
        "structured_lists": [ {"label": str, "items": [str, ...]}, ... ],
        "departments":     [str, ...],
        "posted_at":       str,
        "_route":          str,           # diagnostic
        "error":           str | None,    # set when source=error
      }
    """
    provider, slug, jid, url = _detect_jd_route(job)

    if provider == "greenhouse" and slug and jid:
        d = _fetch_jd_greenhouse(slug, jid)
        if "_error" not in d:
            return {
                "url": url or d.get("absolute_url"),
                "body_chars": len(d["body"]),
                "_route": "greenhouse-api",
                "error": None,
                **d,
            }
        # Fall through to fetch_url on API miss.
        fail_note = d["_error"]
    elif provider == "lever" and slug and jid:
        d = _fetch_jd_lever(slug, jid)
        if "_error" not in d:
            return {
                "url": url or d.get("absolute_url"),
                "body_chars": len(d["body"]),
                "_route": "lever-api",
                "error": None,
                **d,
            }
        fail_note = d["_error"]
    else:
        fail_note = None

    # Generic HTML fallback. Works for Workday tenants and one-off ATSs.
    if url:
        res = fetch_url(url, max_chars=fallback_max_chars)
        if "error" in res:
            return {
                "url": url, "source": "error", "title": "", "company": "",
                "location": "", "body": "", "body_chars": 0,
                "structured_lists": [], "departments": [], "posted_at": "",
                "_route": "fetch_url-failed",
                "error": res["error"] if "error" in res else fail_note or "unknown",
            }
        return {
            "url": url,
            "source": "fetch_url",
            "title": "",
            "company": (job.get("company") if isinstance(job, dict) else "") or "",
            "location": (job.get("location") if isinstance(job, dict) else "") or "",
            "body": res.get("text", ""),
            "body_chars": len(res.get("text", "")),
            "structured_lists": [],
            "departments": [],
            "posted_at": (job.get("posted_at") if isinstance(job, dict) else "") or "",
            "_route": "fetch_url-fallback" if fail_note else "fetch_url",
            "error": fail_note,  # surface the API miss reason if there was one
        }

    return {
        "url": None, "source": "error", "title": "", "company": "",
        "location": "", "body": "", "body_chars": 0,
        "structured_lists": [], "departments": [], "posted_at": "",
        "_route": "no-input",
        "error": "no url and no resolvable provider id",
    }


_SKILL_VOCAB = {
    "languages": ["python", "go", "rust", "java", "kotlin", "scala", "typescript",
                  "javascript", "sql", "r", "c++", "c#"],
    "data": ["spark", "kafka", "airflow", "dbt", "snowflake", "bigquery",
             "redshift", "databricks", "postgres", "mysql", "mongodb", "cassandra"],
    "cloud": ["aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform"],
    "ml": ["pytorch", "tensorflow", "scikit-learn", "pandas", "numpy", "ml",
           "llm", "rag", "vector"],
}


def parse_jd_skills(text: str) -> dict[str, list[str]]:
    """Extract canonical skill mentions from a JD. Case-insensitive substring."""
    t = (text or "").lower()
    found: dict[str, list[str]] = {bucket: [] for bucket in _SKILL_VOCAB}
    for bucket, vocab in _SKILL_VOCAB.items():
        for term in vocab:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", t):
                found[bucket].append(term)
    return found


def ats_score(resume_text: str, jd_text: str) -> dict[str, Any]:
    """Keyword-overlap ATS-style score. Returns 0.0–1.0 plus matched terms."""
    jd_skills_grouped = parse_jd_skills(jd_text)
    jd_terms = {t for terms in jd_skills_grouped.values() for t in terms}
    if not jd_terms:
        return {"score": 0.0, "matched": [], "missing": [], "jd_terms": []}
    rt = (resume_text or "").lower()
    matched = sorted(t for t in jd_terms if re.search(
        rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", rt))
    return {
        "score": round(len(matched) / len(jd_terms), 3),
        "matched": matched,
        "missing": sorted(jd_terms - set(matched)),
        "jd_terms": sorted(jd_terms),
    }


# ----- STATIC ------------------------------------------------------------

# Small jurisdictional lookup. Real production would hit a legal-DB MCP /
# something curated. Keys match common JD location strings.
_JURISDICTION_TABLE: dict[str, dict[str, Any]] = {
    "us": {
        "frameworks": ["SOC2", "HIPAA (if PHI)", "PCI (if cards)", "CCPA (CA)"],
        "data_residency": "US",
        "notes": "HIPAA only attaches when handling PHI; CCPA covers CA residents.",
    },
    "remote-us": {
        "frameworks": ["SOC2", "HIPAA (if PHI)", "PCI (if cards)", "CCPA (CA)"],
        "data_residency": "US",
        "notes": "Treat as US jurisdiction; some employers exclude specific states.",
    },
    "eu": {
        "frameworks": ["GDPR", "ISO 27001", "DPA"],
        "data_residency": "EU",
        "notes": "GDPR Schrems II — restrict transfers; appoint DPO if scale warrants.",
    },
    "uk": {
        "frameworks": ["UK GDPR", "DPA 2018", "ISO 27001"],
        "data_residency": "UK",
        "notes": "Post-Brexit UK GDPR mirrors EU GDPR with adequacy decision.",
    },
    "nyc": {
        "frameworks": ["SOC2", "NYDFS Cyber (if financial)", "SHIELD Act", "CCPA-aligned"],
        "data_residency": "US",
        "notes": "SHIELD Act applies to NY-resident data regardless of employer location.",
    },
}


def lookup_jurisdiction(location: str) -> dict[str, Any]:
    key = (location or "").strip().lower()
    # Coarse matching: exact, then substring.
    if key in _JURISDICTION_TABLE:
        return {"location": location, **_JURISDICTION_TABLE[key]}
    for k, v in _JURISDICTION_TABLE.items():
        if k in key or key in k:
            return {"location": location, "matched": k, **v}
    return {"location": location, "frameworks": [], "data_residency": "unknown",
            "notes": f"No jurisdiction match for {location!r}; treat as unknown."}


_COMP_BANDS: dict[tuple[str, str], dict[str, int]] = {
    # (role_normalized, region) -> band. Numbers are illustrative midpoints.
    ("senior data engineer", "us"):     {"low": 165000, "mid": 195000, "high": 235000},
    ("staff data engineer", "us"):      {"low": 210000, "mid": 250000, "high": 310000},
    ("senior software engineer", "us"): {"low": 170000, "mid": 200000, "high": 250000},
    ("staff software engineer", "us"):  {"low": 220000, "mid": 270000, "high": 340000},
    ("senior data engineer", "nyc"):    {"low": 185000, "mid": 220000, "high": 270000},
    ("staff data engineer", "nyc"):     {"low": 235000, "mid": 285000, "high": 360000},
}


def lookup_comp_band(role: str, location: str) -> dict[str, Any]:
    role_n = re.sub(r"\s+", " ", (role or "").strip().lower())
    loc_n = (location or "").strip().lower()
    region = "nyc" if "nyc" in loc_n or "new york" in loc_n else "us"
    band = _COMP_BANDS.get((role_n, region))
    if band:
        return {"role": role, "region": region, "currency": "USD", **band,
                "source": "static-band-table"}
    # Soft-match: pick band by seniority hint.
    seniority = "staff" if "staff" in role_n or "principal" in role_n else "senior"
    fallback_role = f"{seniority} software engineer"
    band = _COMP_BANDS.get((fallback_role, region), _COMP_BANDS[("senior software engineer", "us")])
    return {"role": role, "region": region, "currency": "USD", **band,
            "source": f"fallback:{fallback_role}"}


# ----- REAL: Greenhouse + Lever-backed search ---------------------------

# Curated lists of public job boards. All probed live on 2026-04-26 — only
# slugs that returned >0 jobs are kept. Both APIs are free, public, no auth.
# To add boards: probe with curl https://boards-api.greenhouse.io/v1/boards/<slug>/jobs
# (or Lever's /v0/postings/<slug>?mode=json) and append on a 200 with non-empty body.
KNOWN_GREENHOUSE_BOARDS = [
    # AI / ML
    "anthropic", "scaleai", "stripe", "datadog", "discord", "figma",
    "rampnetwork", "brex", "vercel", "databricks",
    # Big consumer
    "airbnb", "robinhood", "coinbase", "instacart", "doordashusa",
    "webflow", "reddit", "cloudflare", "mongodb", "elastic",
    "duolingo", "asana", "lyft", "peloton", "dropbox",
    "zoominfo", "intercom", "postman", "samsara", "fivetran", "fastly",
]

KNOWN_LEVER_BOARDS = ["spotify", "plaid", "clari", "peerspace"]

GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
LEVER_BASE = "https://api.lever.co/v0/postings"


def _gh_fetch_board(company: str, timeout: int = 10) -> list[dict]:
    """Fetch a single Greenhouse board's jobs. Returns [] on any error."""
    try:
        r = requests.get(f"{GREENHOUSE_BASE}/{company}/jobs", timeout=timeout,
                         headers={"User-Agent": "ROBOPORT/0.1"})
        if r.status_code != 200:
            return []
        return r.json().get("jobs", []) or []
    except Exception:  # noqa: BLE001
        return []


def _gh_normalize(job: dict, company: str) -> dict[str, Any]:
    return {
        "id": f"gh-{company}-{job.get('id')}",
        "title": job.get("title", ""),
        "company": company,
        "location": (job.get("location") or {}).get("name", "") or "",
        "source": "greenhouse",
        "source_url": job.get("absolute_url", ""),
        "posted_at": (job.get("updated_at") or "")[:10],
        "raw_description": "",
        "salary_hint": None,
    }


def _lv_fetch_board(company: str, timeout: int = 10) -> list[dict]:
    """Fetch a single Lever board's postings. Returns [] on any error."""
    try:
        r = requests.get(f"{LEVER_BASE}/{company}?mode=json", timeout=timeout,
                         headers={"User-Agent": "ROBOPORT/0.1"})
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _lv_normalize(job: dict, company: str) -> dict[str, Any]:
    cats = job.get("categories") or {}
    # createdAt is unix ms in Lever's API.
    from datetime import datetime, timezone
    posted_at = ""
    ts = job.get("createdAt")
    if isinstance(ts, (int, float)):
        posted_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
    return {
        "id": f"lv-{company}-{job.get('id')}",
        "title": job.get("text", ""),
        "company": company,
        "location": cats.get("location", "") or "",
        "source": "lever",
        "source_url": job.get("hostedUrl", ""),
        "posted_at": posted_at,
        "raw_description": "",
        "salary_hint": None,
    }


def _lv_matches(job: dict, query: str, location: str) -> bool:
    """Title/location matcher adapted to Lever's job shape (text + categories)."""
    pseudo = {"title": job.get("text"), "location": (job.get("categories") or {}).get("location")}
    return _job_matches(pseudo, query, location)


_STOPWORDS = {"the", "and", "for", "with", "any", "all", "job", "jobs"}


def _job_matches(job: dict, query: str, location: str) -> bool:
    q = (query or "").lower().strip()
    title = (job.get("title") or "").lower()
    if q:
        if q in title:
            pass  # full-phrase hit
        else:
            # Multi-word query: ALL meaningful tokens (>2 chars, not stop-only)
            # must appear in the title.
            toks = [t for t in re.split(r"\s+", q) if len(t) > 2]
            meaningful = [t for t in toks if t not in _STOPWORDS] or toks
            if not all(tok in title for tok in meaningful):
                return False
    loc_filter = (location or "").lower().strip()
    if loc_filter and loc_filter not in ("remote-us", "remote us", "us", ""):
        if loc_filter not in (job.get("location") or "").lower():
            return False
    return True


def search_linkedin(query: str, location: str = "Remote-US",
                    limit: int = 25) -> dict[str, Any]:
    """Search across curated Greenhouse boards for matching jobs.

    Note on naming: kept as `search_linkedin` so existing agent specs +
    config/agent_config.yaml whitelists work unchanged. LinkedIn has no
    free public job-search API; this aggregates Greenhouse boards instead.
    """
    results: list[dict] = []
    boards_hit = 0
    boards_total = len(KNOWN_GREENHOUSE_BOARDS) + len(KNOWN_LEVER_BOARDS)
    for company in KNOWN_GREENHOUSE_BOARDS:
        jobs = _gh_fetch_board(company)
        if jobs:
            boards_hit += 1
        for j in jobs:
            if _job_matches(j, query, location):
                results.append(_gh_normalize(j, company))
    for company in KNOWN_LEVER_BOARDS:
        jobs = _lv_fetch_board(company)
        if jobs:
            boards_hit += 1
        for j in jobs:
            if _lv_matches(j, query, location):
                results.append(_lv_normalize(j, company))
    results = results[:limit]
    return {
        "query": query,
        "location": location,
        "boards_searched": boards_hit,
        "boards_total": boards_total,
        "results": results,
        "source": "greenhouse+lever-aggregator",
    }


def search_company_careers(company: str, query: str = "",
                           limit: int = 25) -> dict[str, Any]:
    """Look up a single company's board on Greenhouse, falling back to Lever.

    `company` is the board slug (e.g. 'anthropic', 'spotify'). Empty result
    if the slug doesn't exist on either provider.
    """
    slug = (company or "").strip().lower().replace(" ", "")
    # Greenhouse first.
    jobs = _gh_fetch_board(slug)
    if jobs:
        matched = [j for j in jobs if _job_matches(j, query, "")][:limit]
        return {"company": company, "slug": slug, "query": query,
                "results": [_gh_normalize(j, slug) for j in matched],
                "source": "greenhouse"}
    # Fall back to Lever.
    jobs = _lv_fetch_board(slug)
    if jobs:
        matched = [j for j in jobs if _lv_matches(j, query, "")][:limit]
        return {"company": company, "slug": slug, "query": query,
                "results": [_lv_normalize(j, slug) for j in matched],
                "source": "lever"}
    return {"company": company, "slug": slug, "query": query, "results": [],
            "source": "not-found",
            "_note": "Tried Greenhouse + Lever. Workday boards have per-tenant URLs and aren't supported here yet."}


def search_indeed(query: str, location: str = "Remote-US",
                  limit: int = 25) -> dict[str, Any]:
    """Indeed has no free public API; this aggregates Greenhouse instead.

    Same backend as `search_linkedin` so the agent gets real results
    regardless of which search tool it picks. Source labelled distinctly
    so dedupe still works.
    """
    out = search_linkedin(query, location, limit)
    out["source_alias"] = "indeed-via-greenhouse"
    return out


# ----- REGISTRY + JSON SCHEMAS ------------------------------------------

# Canonical tool implementations. Names match config/agent_config.yaml.
TOOL_FNS: dict[str, Callable[..., Any]] = {
    "load_profile": load_profile,
    "fetch_url": fetch_url,
    "dedupe_jobs": dedupe_jobs,
    "validate_url_active": validate_url_active,
    "fetch_jd_full": fetch_jd_full,
    "parse_jd_skills": parse_jd_skills,
    "ats_score": ats_score,
    "lookup_jurisdiction": lookup_jurisdiction,
    "lookup_comp_band": lookup_comp_band,
    "search_linkedin": search_linkedin,
    "search_indeed": search_indeed,
    "search_company_careers": search_company_careers,
}

# JSON schema definitions for Ollama's `tools` parameter. Shapes match
# `function` definitions in the OpenAI tool-calling convention.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "load_profile": {
        "type": "function",
        "function": {
            "name": "load_profile",
            "description": "Read a candidate or company profile JSON from the repo.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string",
                                        "description": "Repo-relative path, e.g. resources/datasets/profile_example.json"}},
            },
        },
    },
    "fetch_url": {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "GET a URL and return cleaned text content (HTML stripped). Use for generic web pages; prefer fetch_jd_full for actual JD pages.",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
            },
        },
    },
    "dedupe_jobs": {
        "type": "function",
        "function": {
            "name": "dedupe_jobs",
            "description": "Drop duplicate jobs by (title, company, location).",
            "parameters": {
                "type": "object",
                "required": ["jobs"],
                "properties": {"jobs": {"type": "array",
                                        "items": {"type": "object"}}},
            },
        },
    },
    "validate_url_active": {
        "type": "function",
        "function": {
            "name": "validate_url_active",
            "description": "HEAD-check URLs in parallel; drop dead ones before handoff. Accepts a list of URL strings, or a list of Job objects (with source_url), or a search response object. Returns per-URL status plus live/dead counts.",
            "parameters": {
                "type": "object",
                "required": ["urls"],
                "properties": {
                    "urls": {
                        "description": "URLs to check. Accepts: array of strings; array of Job-shaped objects with a source_url field; or a search response with a results array.",
                    },
                    "timeout": {"type": "integer", "description": "Per-URL timeout in seconds (default 5)."},
                    "max_workers": {"type": "integer", "description": "Parallel worker count (default 10)."},
                },
            },
        },
    },
    "fetch_jd_full": {
        "type": "function",
        "function": {
            "name": "fetch_jd_full",
            "description": "Fetch the full job-description body for a single job. Uses Greenhouse / Lever single-job APIs when the source is one of those (returns structured lists of requirements/responsibilities for Lever); falls back to fetch_url + HTML strip for unknown sources. Pass either the Job object (preferred — lets the tool route via gh-/lv- id) or a bare URL.",
            "parameters": {
                "type": "object",
                "required": ["job"],
                "properties": {
                    "job": {
                        "description": "Either a Job-shaped object (with id and/or source_url) or a URL string.",
                    },
                    "fallback_max_chars": {
                        "type": "integer",
                        "description": "Max chars when falling back to fetch_url (default 12000).",
                    },
                },
            },
        },
    },
    "parse_jd_skills": {
        "type": "function",
        "function": {
            "name": "parse_jd_skills",
            "description": "Extract canonical skills (languages/data/cloud/ml) from JD text.",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
    },
    "ats_score": {
        "type": "function",
        "function": {
            "name": "ats_score",
            "description": "Keyword-overlap score of a resume against a JD (0.0–1.0).",
            "parameters": {
                "type": "object",
                "required": ["resume_text", "jd_text"],
                "properties": {"resume_text": {"type": "string"},
                               "jd_text": {"type": "string"}},
            },
        },
    },
    "lookup_jurisdiction": {
        "type": "function",
        "function": {
            "name": "lookup_jurisdiction",
            "description": "Map a location string to applicable compliance frameworks.",
            "parameters": {
                "type": "object",
                "required": ["location"],
                "properties": {"location": {"type": "string"}},
            },
        },
    },
    "lookup_comp_band": {
        "type": "function",
        "function": {
            "name": "lookup_comp_band",
            "description": "Look up a salary band for a role + location (USD).",
            "parameters": {
                "type": "object",
                "required": ["role", "location"],
                "properties": {"role": {"type": "string"},
                               "location": {"type": "string"}},
            },
        },
    },
    "search_linkedin": {
        "type": "function",
        "function": {
            "name": "search_linkedin",
            "description": "Search Greenhouse + Lever job boards (LinkedIn has no free API; name preserved for compatibility).",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"},
                               "location": {"type": "string"}},
            },
        },
    },
    "search_indeed": {
        "type": "function",
        "function": {
            "name": "search_indeed",
            "description": "Same backend as search_linkedin (Indeed has no free API). Source labelled 'indeed-via-greenhouse'.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"},
                               "location": {"type": "string"}},
            },
        },
    },
    "search_company_careers": {
        "type": "function",
        "function": {
            "name": "search_company_careers",
            "description": "Search a single company's Greenhouse or Lever board by slug.",
            "parameters": {
                "type": "object",
                "required": ["company"],
                "properties": {"company": {"type": "string"},
                               "query": {"type": "string"}},
            },
        },
    },
}


def dispatch(name: str, arguments: dict[str, Any]) -> Any:
    """Execute a tool call. Returns a JSON-serializable result or an error dict."""
    fn = TOOL_FNS.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(**(arguments or {}))
    except TypeError as e:
        return {"error": f"bad arguments to {name}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{name} raised: {e!r}"}


def schemas_for(allowed: list[str]) -> list[dict]:
    return [TOOL_SCHEMAS[name] for name in allowed if name in TOOL_SCHEMAS]


def load_agent_tool_map() -> dict[str, list[str]]:
    """Read config/agent_config.yaml -> {agent_id: [tool_name, ...]}."""
    import yaml  # local import: yaml only needed when --live

    cfg = yaml.safe_load((REPO / "config" / "agent_config.yaml").read_text(encoding="utf-8"))
    return {k: list(v or []) for k, v in (cfg.get("tools") or {}).items()}
