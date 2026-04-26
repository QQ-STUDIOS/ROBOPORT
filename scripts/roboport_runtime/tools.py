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


# ----- REAL: Greenhouse-backed search -----------------------------------

# Curated list of public Greenhouse boards. The Greenhouse Job Board API is
# free, public, no auth, no rate limiting beyond reasonable use. Add boards
# here to broaden `search_linkedin`'s coverage.
KNOWN_GREENHOUSE_BOARDS = [
    "anthropic", "openai", "stripe", "datadog", "discord", "figma",
    "ramp", "brex", "vercel", "scale", "perplexityai", "mistralai",
    "huggingface", "cohere", "writer", "pinecone", "weaviate",
    "snowflake", "databricks", "duckdb", "airbyte", "dbtlabs",
    "airbnb", "doordash", "robinhood", "coinbase", "instacart",
]

GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"


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
        "raw_description": "",  # populate via fetch_url if needed
        "salary_hint": None,
    }


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
    for company in KNOWN_GREENHOUSE_BOARDS:
        jobs = _gh_fetch_board(company)
        if jobs:
            boards_hit += 1
        for j in jobs:
            if _job_matches(j, query, location):
                results.append(_gh_normalize(j, company))
    results = results[:limit]
    return {
        "query": query,
        "location": location,
        "boards_searched": boards_hit,
        "boards_total": len(KNOWN_GREENHOUSE_BOARDS),
        "results": results,
        "source": "greenhouse-aggregator",
    }


def search_company_careers(company: str, query: str = "",
                           limit: int = 25) -> dict[str, Any]:
    """Look up a single company's Greenhouse board.

    The `company` arg is the Greenhouse board slug (e.g. 'anthropic'). If
    the slug doesn't exist on Greenhouse the result is empty.
    """
    slug = (company or "").strip().lower().replace(" ", "")
    jobs = _gh_fetch_board(slug)
    matched = [j for j in jobs if _job_matches(j, query, "")][:limit]
    return {
        "company": company,
        "slug": slug,
        "query": query,
        "results": [_gh_normalize(j, slug) for j in matched],
        "source": "greenhouse" if jobs else "greenhouse:not-found",
    }


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
            "description": "GET a URL and return cleaned text content (HTML stripped).",
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
            "description": "Search LinkedIn for jobs (sample data — see _stub_note).",
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
            "description": "Search Indeed for jobs (sample data — see _stub_note).",
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
            "description": "Search a company's careers page (sample data — see _stub_note).",
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
