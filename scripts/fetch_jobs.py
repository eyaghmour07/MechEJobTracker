#!/usr/bin/env python3
"""Fetch ME internships and new-grad roles from public ATS APIs and rewrite markdown tables."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "companies.yaml"
APPLIED_PATH = Path(__file__).resolve().parent / "applied.yaml"
JOBS_JSON_PATH = ROOT / "docs" / "jobs.json"
MAX_AGE_DAYS = 120
WORKDAY_LIMIT = 20
WORKDAY_MAX_PAGES = 3
WORKDAY_QUERIES = (
    "engineering intern",
    "mechanical intern",
    "manufacturing intern",
    "operations intern",
    "engineering internship",
    "engineering co-op",
    "mechanical engineer",
    "new grad",
)
HTTP_TIMEOUT = 25
MAX_WORKERS = 10

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

CATEGORIES = [
    ("medical", "Medical Devices", "TABLE_MEDICAL"),
    ("automotive", "Automotive & EV", "TABLE_AUTOMOTIVE"),
    ("aerospace", "Aerospace & Defense", "TABLE_AEROSPACE"),
    ("energy", "Energy & Industrial", "TABLE_ENERGY"),
    ("robotics", "Robotics & Hardware", "TABLE_ROBOTICS"),
    ("other", "Other", "TABLE_OTHER"),
]

MD_FILES = {
    ("intern", "usa"): ROOT / "README.md",
    ("newgrad", "usa"): ROOT / "NEW_GRAD_USA.md",
    ("intern", "intl"): ROOT / "INTERN_INTL.md",
    ("newgrad", "intl"): ROOT / "NEW_GRAD_INTL.md",
}

COUNT_KEYS = {
    ("intern", "usa"): "COUNT_INTERN_USA",
    ("newgrad", "usa"): "COUNT_NEWGRAD_USA",
    ("intern", "intl"): "COUNT_INTERN_INTL",
    ("newgrad", "intl"): "COUNT_NEWGRAD_INTL",
}

APPLIED_COUNT_KEYS = {
    ("intern", "usa"): "COUNT_APPLIED_INTERN_USA",
    ("newgrad", "usa"): "COUNT_APPLIED_NEWGRAD_USA",
    ("intern", "intl"): "COUNT_APPLIED_INTERN_INTL",
    ("newgrad", "intl"): "COUNT_APPLIED_NEWGRAD_INTL",
}

INCLUDE_RE = re.compile(
    r"""
    mechanical\s+engineer|mechanical\s+design|mechanical\s+engineering|
    manufacturing\s+engineer|manufacturing\s+engineering|
    product\s+design\s+engineer|process\s+engineer|
    thermal\s+engineer|structural\s+engineer|powertrain|propulsion|
    controls\s+engineer|materials\s+engineer|
    mechatronic|aerothermal|aerospace\s+engineer|
    biomedical\s+engineer|biomedical\s+engineering|
    packaging\s+engineer|packaging\s+engineering|reliability\s+engineer|
    design\s+quality|quality\s+engineer|quality\s+engineering|
    r&d\s+engineer|research\s+and\s+development\s+engineer|
    process\s+engineering|test\s+engineer|test\s+engineering|
    hardware\s+engineer|equipment\s+engineer|tooling\s+engineer|
    manufacturing\s+process|mechanical\s+systems|
    fluids?\s+engineer|stress\s+engineer|structures\s+engineer|
    vehicle\s+engineer|chassis|nvh|hvac\s+engineer|
    electro-?mechanical|electromechanical
    """,
    re.I | re.X,
)

EXCLUDE_RE = re.compile(
    r"""
    \b(software\s+engineer|software\s+engineering|software\s+development|software\s+test|firmware|
    \bswe\b|fullstack|full-stack|front-?end|back-?end|
    data\s+scientist|data\s+engineer|data\s+engineering|
    electrical\s+engineer|electrical\s+engineering|
    computer\s+science|machine\s+learning|ml\s+engineer|devops|sre\b|
    android\s+engineer|ios\s+engineer|web\s+developer|
    nurse|nursing|clinician|clinical\s+specialist|sales\s+rep|
    account\s+executive|territory\s+manager|registered\s+nurse|
    technician|finance\s+intern|marketing\s+intern|hr\s+intern)
    \b
    """,
    re.I | re.X,
)

HARDWARE_OVERRIDE_RE = re.compile(
    r"mechanical|manufacturing|mechatronic|hardware|thermal|structural|propulsion|powertrain",
    re.I,
)

# Intern/co-op titles at these companies are often "Engineering Intern" or
# "Operations Internship" without the word mechanical.
INTERN_ME_RE = re.compile(
    r"""
    engineer|engineering|manufacturing|mechanical|mechatronic|
    biomedical|aerospace|operations\s+(intern|internship|co-?op)|
    process|quality|r&d|research\s+and\s+development|industrial|
    validation|packaging|tooling|reliability|materials|thermal|
    hardware|product\s+development|production|plant|factory|npi|
    assembly|equipment|design\s+intern|test\s+intern
    """,
    re.I | re.X,
)
TALENT_POOL_RE = re.compile(
    r"""
    talent\s+pool|
    not\s+currently\s+recruiting|
    not\s+currently\s+hiring|
    building\s+a\s+pipeline\s+for\s+future|
    this\s+is\s+not\s+(an?\s+)?(open\s+)?(req|requisition|vacancy|job\s+posting)|
    expression\s+of\s+interest
    """,
    re.I | re.X,
)
ME_MAJOR_RE = re.compile(
    r"mechanical\s+engineering|mechanical\s+engineer|\bmech\.?\s*e\b|mechatronic",
    re.I,
)
DEGREE_IN_RE = re.compile(
    r"(?:pursuing|enrolled\s+in|seeking|bachelor'?s?|master'?s?|degree|major(?:ing)?)\s+"
    r"(?:a\s+)?(?:bachelor'?s?\s+|master'?s?\s+)?(?:degree\s+)?(?:in|of)\s+([^\.]{8,280})",
    re.I,
)
NAMED_MAJOR_RE = re.compile(
    r"""
    chemical\s+engineering|biomedical\s+engineering|bioengineering|
    electrical\s+engineering|computer\s+science|chemistry|biochemistry|
    biology|industrial\s+engineering|manufacturing\s+engineering|
    plastics\s+engineering|materials\s+engineering|aerospace\s+engineering|
    mechanical\s+engineering|mechatronic
    """,
    re.I | re.X,
)
WET_LAB_RE = re.compile(
    r"chromatography|\bnmr\b|mass\s+spectrom|elisa|western\s+blot|organic\s+synthesis|immunohistochemistry",
    re.I,
)
INTERN_JUNK_RE = re.compile(
    r"""
    \b(it\s+intern|information\s+technology|software|firmware|cyber|
    finance\s+intern|marketing\s+intern|hr\s+intern|human\s+resources|
    commercial|business\s+analyst|government\s+affairs|statistician|
    clinical\s+application|clinical\s+field|sales\s+intern|legal|
    communications|accountant|nursing|nurse|data\s+scientist|
    data\s+engineer|digital\s+marketing)
    \b
    """,
    re.I | re.X,
)

COMPANY_PRIORITY = [
    "Johnson & Johnson",
    "Medtronic",
    "Abbott",
    "Stryker",
    "Boston Scientific",
    "Intuitive",
    "GE HealthCare",
    "Thermo Fisher Scientific",
    "Baxter",
    "BD",
    "Philips",
    "Edwards Lifesciences",
    "Zimmer Biomet",
    "Alcon",
    "Dexcom",
    "Danaher",
    "Tesla",
    "General Motors",
    "Ford",
    "Toyota",
    "Honda",
    "BMW",
    "Stellantis",
    "Rivian",
    "Scout Motors",
    "Lucid Motors",
    "Cummins",
    "SpaceX",
    "Boeing",
    "Blue Origin",
    "GE Aerospace",
    "Airbus",
    "John Deere",
    "Caterpillar",
    "Honeywell",
    "3M",
    "GE Vernova",
    "Form Energy",
    "Oklo",
    "Emerson",
    "Eaton",
    "Parker Hannifin",
    "Applied Materials",
    "ASML",
    "Lam Research",
    "Boston Dynamics",
    "Formlabs",
    "NVIDIA",
    "Procter & Gamble",
]

COOP_RE = re.compile(r"\bco[-\s]?op\b", re.I)
INTERN_RE = re.compile(r"\b(intern|internship|interns|student)\b", re.I)
NEWGRAD_RE = re.compile(
    r"""
    new\s+grad|new\s+graduate|early\s+career|university\s+grad|
    recent\s+grad|graduate\s+program|graduating|class\s+of\s+20|
    rotational|campus\s+hire|entry[\s-]?level|associate\s+engineer|
    engineer\s+i\b|engineer\s+1\b
    """,
    re.I | re.X,
)
SENIOR_RE = re.compile(
    r"""
    \b(senior|staff|principal|lead|manager|director|head\s+of|
    sr\.|sr\b|iii\b|iv\b|vp\b|chief)\b
    """,
    re.I | re.X,
)
YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:\+|plus)?\s*years?", re.I)

US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
}
US_HINTS = {
    "usa", "u.s.", "u.s.a.", "u.s.a", "united states", "united states of america",
    "us-remote", "remote-us", "remote us", "remote, us", "united states of am",
}
INTL_HINTS = {
    "canada", "ontario", "quebec", "british columbia", "toronto", "montreal",
    "vancouver", "united kingdom", "uk", "england", "scotland", "london",
    "germany", "france", "italy", "spain", "netherlands", "sweden", "norway",
    "denmark", "finland", "switzerland", "austria", "belgium", "ireland",
    "poland", "india", "china", "japan", "korea", "singapore", "australia",
    "new zealand", "mexico", "brazil", "israel", "uae", "dubai", "taiwan",
    "hong kong", "malaysia", "thailand", "vietnam", "philippines", "indonesia",
    "south africa", "saudi", "egypt", "turkey", "portugal", "czech", "romania",
    "hungary", "slovakia", "munich", "berlin", "paris", "amsterdam", "dublin",
    "sydney", "melbourne", "bangalore", "bengaluru", "hyderabad", "shanghai",
    "shenzhen", "beijing", "tokyo", "osaka", "seoul", "remote - canada",
    "remote - europe", "remote - uk", "emea", "apac",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_jobs")

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    }
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    relative = parse_workday_relative(text)
    if relative is not None:
        return relative
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_workday_relative(text: str) -> datetime | None:
    lower = text.lower()
    today = now_utc()
    if "posted today" in lower or lower == "today":
        return today
    if "yesterday" in lower:
        return today.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"(\d+)\+?\s*days?\s+ago", lower)
    if m:
        days = int(m.group(1))
        return datetime.fromtimestamp(today.timestamp() - days * 86400, tz=timezone.utc)
    m = re.search(r"(\d+)\s*hours?\s+ago", lower)
    if m:
        return today
    return None


def age_days(posted: datetime | None) -> int | None:
    if posted is None:
        return None
    delta = now_utc() - posted
    return max(0, delta.days)


def get_json(url: str, **kwargs: Any) -> Any:
    resp = SESSION.get(url, timeout=HTTP_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    resp = SESSION.post(url, json=payload, timeout=HTTP_TIMEOUT, headers=headers, **kwargs)
    resp.raise_for_status()
    return resp.json()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def fetch_greenhouse(company: dict[str, Any]) -> list[dict[str, Any]]:
    board = company["board"]
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
    jobs = []
    for job in data.get("jobs", []):
        loc = (job.get("location") or {}).get("name") or ""
        jobs.append(
            {
                "title": job.get("title") or "",
                "location": loc,
                "url": job.get("absolute_url") or "",
                "posted_at": parse_dt(job.get("first_published") or job.get("updated_at")),
                "description": strip_html(job.get("content") or ""),
            }
        )
    return jobs


def fetch_lever(company: dict[str, Any]) -> list[dict[str, Any]]:
    board = company["board"]
    data = get_json(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
    jobs = []
    for job in data:
        cats = job.get("categories") or {}
        jobs.append(
            {
                "title": job.get("text") or "",
                "location": cats.get("location") or job.get("country") or "",
                "url": job.get("hostedUrl") or job.get("applyUrl") or "",
                "posted_at": parse_dt(job.get("createdAt")),
                "description": strip_html(job.get("descriptionPlain") or job.get("description") or ""),
            }
        )
    return jobs


def fetch_ashby(company: dict[str, Any]) -> list[dict[str, Any]]:
    board = company["board"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    jobs = []
    for job in data.get("jobs", []):
        loc = job.get("location") or ""
        if isinstance(loc, dict):
            loc = loc.get("locationName") or loc.get("name") or ""
        jobs.append(
            {
                "title": job.get("title") or "",
                "location": loc,
                "url": job.get("jobUrl") or job.get("applyUrl") or "",
                "posted_at": parse_dt(job.get("publishedAt") or job.get("updatedAt")),
                "description": strip_html(job.get("descriptionHtml") or job.get("descriptionPlain") or ""),
            }
        )
    return jobs


def fetch_smartrecruiters(company: dict[str, Any]) -> list[dict[str, Any]]:
    board = company["board"]
    jobs: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = get_json(
            f"https://api.smartrecruiters.com/v1/companies/{board}/postings",
            params={"limit": 100, "offset": offset},
        )
        items = data.get("content") or []
        for job in items:
            loc = job.get("location") or {}
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            jobs.append(
                {
                    "title": job.get("name") or "",
                    "location": ", ".join(p for p in parts if p),
                    "url": job.get("ref") or job.get("applyUrl") or "",
                    "posted_at": parse_dt(job.get("releasedDate") or job.get("createdOn")),
                    "description": "",
                }
            )
        if len(items) < 100:
            break
        offset += 100
        if offset > 500:
            break
    return jobs


def fetch_workday(company: dict[str, Any]) -> list[dict[str, Any]]:
    host = company["host"]
    tenant = company["tenant"]
    site = company["site"]
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen: set[str] = set()
    jobs: list[dict[str, Any]] = []

    for query in WORKDAY_QUERIES:
        offset = 0
        for _ in range(WORKDAY_MAX_PAGES):
            payload = {
                "appliedFacets": {},
                "limit": WORKDAY_LIMIT,
                "offset": offset,
                "searchText": query,
            }
            try:
                data = post_json(url, payload)
            except requests.HTTPError:
                if not jobs and query == WORKDAY_QUERIES[0]:
                    raise
                break
            postings = data.get("jobPostings") or []
            if not postings:
                break
            for job in postings:
                path = job.get("externalPath") or job.get("bulletFields") or ""
                key = f"{job.get('title')}|{job.get('locationsText')}|{path}"
                if key in seen:
                    continue
                seen.add(key)
                apply_url = f"https://{host}/en-US/{site}{path}" if path else f"https://{host}/{site}"
                detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{path}" if path else ""
                jobs.append(
                    {
                        "title": job.get("title") or "",
                        "location": job.get("locationsText") or "",
                        "url": apply_url,
                        "posted_at": parse_dt(job.get("postedOn") or job.get("firstPosted")),
                        "description": "",
                        "detail_url": detail_url,
                    }
                )
            if len(postings) < WORKDAY_LIMIT:
                break
            offset += WORKDAY_LIMIT
    return jobs


def fetch_tesla(company: dict[str, Any]) -> list[dict[str, Any]]:
    data = get_json("https://www.tesla.com/cua-api/apps/careers/state")
    listings = data.get("listings") or data.get("results") or []
    lookup = data.get("lookup") or {}
    loc_map = lookup.get("locations") or lookup.get("l") or {}
    jobs = []
    for job in listings:
        loc_id = job.get("l") or job.get("location")
        loc = ""
        if isinstance(loc_id, list):
            loc = ", ".join(str(loc_map.get(str(i), i)) for i in loc_id[:3])
        elif isinstance(loc_id, (str, int)):
            loc = str(loc_map.get(str(loc_id), loc_id))
        elif isinstance(job.get("locationName"), str):
            loc = job["locationName"]
        title = job.get("t") or job.get("title") or ""
        job_id = str(job.get("id") or job.get("i") or "")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        url = f"https://www.tesla.com/careers/search/job/{slug}-{job_id}" if job_id else "https://www.tesla.com/careers"
        jobs.append(
            {
                "title": title,
                "location": loc,
                "url": url,
                "posted_at": parse_dt(job.get("dp") or job.get("publishedDate") or job.get("hot")),
                "description": "",
            }
        )
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "tesla": fetch_tesla,
}


def is_internish(title: str) -> bool:
    return bool(COOP_RE.search(title) or INTERN_RE.search(title))


def fetch_workday_detail(detail_url: str) -> tuple[str, bool | None]:
    if not detail_url:
        return "", None
    try:
        data = get_json(detail_url)
    except Exception:  # noqa: BLE001 — fail soft; keep title-only fallback
        return "", None
    info = data.get("jobPostingInfo") or {}
    return strip_html(info.get("jobDescription") or ""), info.get("canApply")


def is_open_posting(title: str, description: str, can_apply: bool | None) -> bool:
    blob = f"{title} {description}"
    if can_apply is False:
        return False
    if TALENT_POOL_RE.search(blob):
        return False
    return True


def allows_mechanical_major(title: str, description: str) -> bool:
    """Keep roles that list ME, or don't name a non-ME-only major list."""
    blob = f"{title} {description}"
    title_has_me = bool(re.search(r"\bmechanical\b|mechatronic", title, re.I))
    desc_has_me = bool(ME_MAJOR_RE.search(blob))
    if title_has_me or desc_has_me:
        # Still drop if a degree sentence names majors and omits ME.
        for match in DEGREE_IN_RE.finditer(blob):
            clause = match.group(1)
            named = {m.group(0).lower() for m in NAMED_MAJOR_RE.finditer(clause)}
            if not named:
                continue
            if not any("mechanical" in n or "mechatronic" in n for n in named):
                return False
        return True
    for match in DEGREE_IN_RE.finditer(blob):
        clause = match.group(1)
        named = {m.group(0).lower() for m in NAMED_MAJOR_RE.finditer(clause)}
        if named and not any("mechanical" in n or "mechatronic" in n for n in named):
            return False
    if WET_LAB_RE.search(blob) and not desc_has_me:
        return False
    if re.search(r"\bbiomedical\b|\bbioengineering\b", title, re.I) and not desc_has_me:
        return False
    return True


def is_me_role(title: str, description: str) -> bool:
    if re.search(r"\btechnician\b", title, re.I):
        return False
    if re.search(r"\bsoftware\b", title, re.I) and not HARDWARE_OVERRIDE_RE.search(title):
        return False
    if EXCLUDE_RE.search(title) and not HARDWARE_OVERRIDE_RE.search(title):
        return False
    if INCLUDE_RE.search(title):
        return True
    if re.search(r"\b(mechanical|mechatronics|manufacturing|aerospace)\b", title, re.I):
        return True
    if is_internish(title) and INTERN_ME_RE.search(title) and not INTERN_JUNK_RE.search(title):
        return True
    return False


def company_priority(name: str) -> int:
    try:
        return COMPANY_PRIORITY.index(name)
    except ValueError:
        return len(COMPANY_PRIORITY) + 1


def markdown_level(level: str) -> str:
    """Markdown backup still groups co-ops with internships."""
    return "intern" if level == "coop" else level


def classify_level(title: str, description: str) -> str | None:
    blob = f"{title} {description[:2500]}"
    if COOP_RE.search(title):
        return "coop"
    if INTERN_RE.search(title):
        return "intern"
    if SENIOR_RE.search(title):
        return None
    years = [int(m.group(1)) for m in YEARS_RE.finditer(blob)]
    if years and min(years) >= 3 and not NEWGRAD_RE.search(title):
        return None
    if NEWGRAD_RE.search(title) or NEWGRAD_RE.search(blob[:800]):
        return "newgrad"
    return None


def classify_geo(location: str) -> str:
    loc = (location or "").strip().lower()
    if not loc:
        return "usa"
    if re.search(r"\d+\s+locations?\b", loc):
        return "usa"
    if any(h in loc for h in INTL_HINTS):
        return "intl"
    if re.search(r"\b(mexico|canada|uk|germany|india|china|japan|france|australia|spain|ireland|netherlands|singapore|vietnam)\b", loc):
        return "intl"
    if any(h in loc for h in US_HINTS) or "united states" in loc:
        return "usa"
    if re.search(r"\bremote\b", loc):
        return "usa"
    m = re.search(r",\s*([a-z]{2})\b", loc)
    if m and m.group(1) in US_STATE_ABBR:
        return "usa"
    for name in US_STATE_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", loc):
            return "usa"
    tokens = set(re.findall(r"[a-z]+", loc))
    if "in" in tokens and not re.search(r",\s*in\b", loc) and "indiana" not in loc:
        tokens.discard("in")
    if "or" in tokens and not re.search(r",\s*or\b", loc) and "oregon" not in loc:
        tokens.discard("or")
    if tokens & US_STATE_ABBR:
        return "usa"
    if re.search(r",\s*[a-z]{2}$", loc) and loc.replace(".", "")[-2:] in US_STATE_ABBR:
        return "usa"
    return "intl"


def fetch_company(company: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
    name = company.get("name", "?")
    ats = (company.get("ats") or "").lower()
    fetcher = FETCHERS.get(ats)
    if not fetcher:
        return name, [], f"unknown ats {ats!r}"
    try:
        raw = fetcher(company)
    except Exception as exc:  # noqa: BLE001 — fail soft per company
        return name, [], f"{type(exc).__name__}: {exc}"
    out = []
    for job in raw:
        title = (job.get("title") or "").strip()
        location = (job.get("location") or "").strip()
        url = (job.get("url") or "").strip()
        desc = job.get("description") or ""
        can_apply = job.get("can_apply")
        if not title or not url:
            continue
        if not desc and job.get("detail_url") and is_internish(title):
            desc, can_apply = fetch_workday_detail(job["detail_url"])
        if not is_open_posting(title, desc, can_apply):
            continue
        if not is_me_role(title, desc):
            continue
        if not allows_mechanical_major(title, desc):
            continue
        level = classify_level(title, desc)
        if not level:
            continue
        posted = job.get("posted_at")
        age = age_days(posted) if isinstance(posted, datetime) else None
        if age is not None and age > MAX_AGE_DAYS:
            continue
        out.append(
            {
                "company": name,
                "careers_url": company.get("careers_url") or url,
                "category": company.get("category") or "other",
                "title": title,
                "location": location or "Not specified",
                "url": url,
                "posted_at": posted,
                "age": age,
                "level": level,
                "geo": classify_geo(location),
            }
        )
    return name, out, None


def dedupe(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for job in jobs:
        key = "|".join(
            [
                job["company"].lower(),
                re.sub(r"\s+", " ", job["title"].lower()),
                re.sub(r"\s+", " ", job["location"].lower()),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def sort_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(job: dict[str, Any]) -> tuple[int, str, float]:
        age = job["age"]
        age_key = float(age) if age is not None else 999.0
        return (company_priority(job["company"]), job["company"].lower(), age_key)

    return sorted(jobs, key=key)


def md_escape(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = url.split("#")[0].split("?")[0]
    return url.rstrip("/").lower()


def url_job_token(url: str) -> str:
    path = normalize_url(url).rsplit("/", 1)[-1]
    return path


def records_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_url = normalize_url(left.get("url") or "")
    right_url = normalize_url(right.get("url") or "")
    if left_url and right_url and left_url == right_url:
        return True
    if left_url and right_url:
        left_tok, right_tok = url_job_token(left_url), url_job_token(right_url)
        if left_tok and left_tok == right_tok and len(left_tok) >= 8:
            return True
    left_co = (left.get("company") or "").strip().lower()
    right_co = (right.get("company") or "").strip().lower()
    left_title = re.sub(r"\s+", " ", (left.get("title") or "").strip().lower())
    right_title = re.sub(r"\s+", " ", (right.get("title") or "").strip().lower())
    return bool(left_co and left_title and left_co == right_co and left_title == right_title)


def load_applied() -> list[dict[str, Any]]:
    if not APPLIED_PATH.exists():
        return []
    data = yaml.safe_load(APPLIED_PATH.read_text(encoding="utf-8")) or {}
    raw = data.get("applications") or data.get("urls") or []
    apps: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            apps.append({"url": item.strip()})
        elif isinstance(item, dict) and (item.get("url") or item.get("title")):
            apps.append(dict(item))
    return apps


def save_applied(apps: list[dict[str, Any]]) -> None:
    header = (
        "# Jobs you have already applied to.\n"
        "# Add a posting URL, or run:\n"
        "#   python scripts/fetch_jobs.py --applied 'https://...'\n"
        "# GitHub: Actions → Update job listings → Run workflow → paste the Apply URL.\n"
        "# Applied roles move under each section's Applied table and stay there even if the posting comes down.\n\n"
    )
    payload = {"applications": apps}
    APPLIED_PATH.write_text(
        header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def add_applied_urls(urls: list[str], apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = now_utc().date().isoformat()
    for url in urls:
        url = url.strip()
        if not url:
            continue
        if any(records_match({"url": url}, app) for app in apps):
            log.info("Already marked applied: %s", url)
            continue
        apps.append({"url": url, "applied_at": today})
        log.info("Marked applied: %s", url)
    return apps


def enrich_applied(apps: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = now_utc().date().isoformat()
    for job in jobs:
        for app in apps:
            if not records_match(job, app):
                continue
            app["url"] = job["url"]
            app["company"] = job["company"]
            app["title"] = job["title"]
            app["location"] = job["location"]
            app["category"] = job["category"]
            app["level"] = job["level"]
            app["geo"] = job["geo"]
            app["careers_url"] = job["careers_url"]
            app.setdefault("applied_at", today)
            if job.get("age") is not None:
                app["age"] = job["age"]
    return apps


def is_applied(job: dict[str, Any], apps: list[dict[str, Any]]) -> bool:
    return any(records_match(job, app) for app in apps)


def snapshot_to_job(app: dict[str, Any]) -> dict[str, Any] | None:
    if not app.get("title") or not app.get("company"):
        return None
    return {
        "company": app.get("company") or "Unknown",
        "careers_url": app.get("careers_url") or app.get("url") or "#",
        "category": app.get("category") or "other",
        "title": app.get("title") or "",
        "location": app.get("location") or "Not specified",
        "url": app.get("url") or app.get("careers_url") or "#",
        "age": app.get("age"),
        "level": app.get("level") or "intern",
        "geo": app.get("geo") or "usa",
    }


def render_table(jobs: list[dict[str, Any]], *, applied: bool = False) -> str:
    action = "Applied" if applied else "Apply"
    lines = [
        f"| Company | Position | Location | {action} | Age |",
        "|---|---|---|---|---|",
    ]
    if not jobs:
        return "\n".join(lines) + "\n"
    for job in jobs:
        company_html = (
            f'<a href="{html.escape(job["careers_url"], quote=True)}">'
            f"<strong>{html.escape(job['company'])}</strong></a>"
        )
        if applied:
            action_html = (
                f'<a href="{html.escape(job["url"], quote=True)}">✅</a>'
            )
        else:
            action_html = (
                f'<a href="{html.escape(job["url"], quote=True)}">'
                f'<img src="https://img.shields.io/badge/Apply-2563eb?style=flat" alt="Apply"></a>'
            )
        age = f"{job['age']}d" if job["age"] is not None else "?"
        lines.append(
            f"| {company_html} | {md_escape(job['title'])} | {md_escape(job['location'])} | {action_html} | {age} |"
        )
    return "\n".join(lines) + "\n"


def replace_block(text: str, start: str, end: str, inner: str) -> str:
    pattern = re.compile(
        rf"(<!-- {re.escape(start)} -->)(.*?)(<!-- {re.escape(end)} -->)",
        re.S,
    )
    replacement = rf"\1\n{inner.rstrip()}\n\3"
    new, n = pattern.subn(replacement, text, count=1)
    if n != 1:
        raise RuntimeError(f"Could not find markers {start}/{end}")
    return new


def replace_count(text: str, key: str, value: int) -> str:
    pattern = re.compile(
        rf"(<!-- {re.escape(key)} -->)(.*?)(<!-- {re.escape(key)}_END -->)",
        re.S,
    )
    replacement = rf"\1**{value}**\3"
    new, n = pattern.subn(replacement, text, count=1)
    if n != 1:
        raise RuntimeError(f"Could not find count markers {key}")
    return new


def write_markdown(
    all_jobs: list[dict[str, Any]],
    apps: list[dict[str, Any]],
) -> None:
    open_buckets: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in MD_FILES}
    applied_buckets: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in MD_FILES}

    for job in all_jobs:
        key = (markdown_level(job["level"]), job["geo"])
        if key not in open_buckets:
            continue
        if is_applied(job, apps):
            applied_buckets[key].append(job)
        else:
            open_buckets[key].append(job)

    # Keep applied snapshots even after the posting disappears from the ATS.
    for app in apps:
        snap = snapshot_to_job(app)
        if not snap:
            continue
        key = (markdown_level(snap["level"]), snap["geo"])
        if key not in applied_buckets:
            continue
        if any(records_match(snap, job) for job in applied_buckets[key]):
            continue
        applied_buckets[key].append(snap)

    open_counts = {key: len(jobs) for key, jobs in open_buckets.items()}
    applied_counts = {key: len(jobs) for key, jobs in applied_buckets.items()}
    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    last_updated = f"*Last updated: {stamp}*"

    for key, path in MD_FILES.items():
        text = path.read_text(encoding="utf-8")
        open_by_cat: dict[str, list[dict[str, Any]]] = {c[0]: [] for c in CATEGORIES}
        applied_by_cat: dict[str, list[dict[str, Any]]] = {c[0]: [] for c in CATEGORIES}
        for job in open_buckets[key]:
            cat = job["category"] if job["category"] in open_by_cat else "other"
            open_by_cat[cat].append(job)
        for job in applied_buckets[key]:
            cat = job["category"] if job["category"] in applied_by_cat else "other"
            applied_by_cat[cat].append(job)
        for cat_id, _label, marker in CATEGORIES:
            text = replace_block(
                text, f"{marker}_START", f"{marker}_END", render_table(sort_jobs(open_by_cat[cat_id]))
            )
            text = replace_block(
                text,
                f"{marker}_APPLIED_START",
                f"{marker}_APPLIED_END",
                render_table(sort_jobs(applied_by_cat[cat_id]), applied=True),
            )
        for count_key, marker in COUNT_KEYS.items():
            text = replace_count(text, marker, open_counts.get(count_key, 0))
        for count_key, marker in APPLIED_COUNT_KEYS.items():
            text = replace_count(text, marker, applied_counts.get(count_key, 0))
        if "LAST_UPDATED_START" in text:
            text = replace_block(text, "LAST_UPDATED_START", "LAST_UPDATED_END", last_updated)
        path.write_text(text, encoding="utf-8")
        log.info(
            "Wrote %s (%s open, %s applied)",
            path.name,
            open_counts.get(key, 0),
            applied_counts.get(key, 0),
        )


def write_jobs_json(all_jobs: list[dict[str, Any]], stamp: str) -> None:
    JOBS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": stamp,
        "jobs": [
            {
                "company": job["company"],
                "title": job["title"],
                "location": job["location"],
                "url": job["url"],
                "careers_url": job["careers_url"],
                "category": job["category"],
                "level": job["level"],
                "geo": job["geo"],
                "age": job["age"],
                "priority": company_priority(job["company"]),
            }
            for job in all_jobs
        ],
    }
    JOBS_JSON_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Wrote %s (%s jobs)", JOBS_JSON_PATH.relative_to(ROOT), len(all_jobs))


def load_companies() -> list[dict[str, Any]]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    companies = data.get("companies") or []
    # Dedupe by name+category in case the YAML is edited twice.
    seen: set[tuple[str, str]] = set()
    out = []
    for company in companies:
        key = (company.get("name", ""), company.get("category", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(company)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch ME jobs and rewrite markdown tables.")
    parser.add_argument(
        "--applied",
        action="append",
        default=[],
        help="Mark a job posting URL as applied (repeatable). Moves it to that section's Applied table.",
    )
    args = parser.parse_args()

    apps = load_applied()
    if args.applied:
        apps = add_applied_urls(args.applied, apps)
        save_applied(apps)

    companies = load_companies()
    log.info("Fetching %s companies", len(companies))
    jobs: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_company, c): c for c in companies}
        for fut in as_completed(futures):
            name, found, err = fut.result()
            if err:
                failures.append(f"{name}: {err}")
                log.warning("FAIL %s — %s", name, err)
            else:
                log.info("OK   %s — %s matching roles", name, len(found))
                jobs.extend(found)

    jobs = dedupe(jobs)
    log.info("Total matching roles after dedupe: %s", len(jobs))
    if failures:
        log.warning("%s companies failed", len(failures))
    apps = enrich_applied(apps, jobs)
    save_applied(apps)
    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    write_jobs_json(jobs, stamp)
    write_markdown(jobs, apps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
