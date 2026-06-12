"""SQLite storage layer for raw and normalised listings.

Schema (3 tables):
  raw_listings        — one row per adapter fetch; INSERT OR IGNORE on re-scrape.
  normalized_listings — canonical extracted records; INSERT OR REPLACE (upserts
                        last_seen_at on re-scrape of the same listing_id).
  dedup_groups        — maps merged listing_ids to their canonical listing_id.

All timestamps stored as ISO-8601 UTC strings.
list fields (source_urls) stored as JSON text.
DB path: ./data/mechpm.sqlite by default; override via Repo(db_path=...).

Run as a script for a smoke test:
  python -m mechpm.storage.sqlite      (from repo root with package installed)

2026-06-12
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mechpm.adapters.base import RawListing
from mechpm.models import NormalizedListing

logger = logging.getLogger("mechpm.storage")

_DEFAULT_DB_PATH: Path = Path("data") / "mechpm.sqlite"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_RAW_LISTINGS = """
CREATE TABLE IF NOT EXISTS raw_listings (
    id                 TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    source_listing_id  TEXT NOT NULL,
    url                TEXT NOT NULL,
    title              TEXT NOT NULL,
    employer           TEXT,
    agency             TEXT,
    location_raw       TEXT,
    posted_at          TEXT,
    salary_raw         TEXT,
    contract_type_raw  TEXT,
    description_raw    TEXT,
    metadata_json      TEXT,
    fetched_at         TEXT NOT NULL,
    UNIQUE(source, source_listing_id)
);
"""

_DDL_NORMALIZED_LISTINGS = """
CREATE TABLE IF NOT EXISTS normalized_listings (
    listing_id          TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    source_listing_id   TEXT NOT NULL,
    source_url          TEXT NOT NULL DEFAULT '',
    source_urls_json    TEXT NOT NULL DEFAULT '[]',
    title               TEXT NOT NULL,
    employer            TEXT,
    agency              TEXT,
    location            TEXT NOT NULL DEFAULT '',
    location_normalized TEXT NOT NULL DEFAULT '',
    country             TEXT NOT NULL DEFAULT 'GB',
    posted_at           TEXT,
    start_date_raw      TEXT,
    start_date          TEXT,
    asap_flag           INTEGER NOT NULL DEFAULT 0,
    duration_raw        TEXT,
    duration_weeks      INTEGER,
    day_rate_min        REAL,
    day_rate_max        REAL,
    rate_currency       TEXT NOT NULL DEFAULT 'GBP',
    rate_period         TEXT,
    ir35_status         TEXT,
    contract_type       TEXT NOT NULL DEFAULT 'contract',
    remote_policy       TEXT,
    sector              TEXT NOT NULL DEFAULT 'generalist',
    description_raw     TEXT,
    description_clean   TEXT,
    discovered_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    is_new_listing      INTEGER NOT NULL DEFAULT 0,
    sanity_flags_json   TEXT NOT NULL DEFAULT '[]'
);
"""

_DDL_DEDUP_GROUPS = """
CREATE TABLE IF NOT EXISTS dedup_groups (
    group_id           TEXT NOT NULL,
    member_listing_id  TEXT NOT NULL,
    PRIMARY KEY (group_id, member_listing_id)
);
"""


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------

class Repo:
    """SQLite-backed repository for raw and normalised listings.

    Usage:
        repo = Repo()                         # uses ./data/mechpm.sqlite
        repo = Repo(":memory:")               # in-memory (tests / smoke)
        repo = Repo("/path/to/custom.sqlite")
    """

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_DDL_RAW_LISTINGS)
            self._conn.execute(_DDL_NORMALIZED_LISTINGS)
            self._conn.execute(_DDL_DEDUP_GROUPS)

    # ------------------------------------------------------------------
    # Insert raw
    # ------------------------------------------------------------------

    def insert_raw(self, raws: list[RawListing]) -> int:
        """Insert raw listings; silently skips duplicates (INSERT OR IGNORE).

        Returns the count of newly inserted rows.
        """
        inserted = 0
        with self._conn:
            for r in raws:
                row_id = hashlib.sha256(
                    f"{r.source}|{r.source_listing_id}".encode()
                ).hexdigest()[:16]
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO raw_listings
                      (id, source, source_listing_id, url, title, employer, agency,
                       location_raw, posted_at, salary_raw, contract_type_raw,
                       description_raw, metadata_json, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row_id,
                        r.source,
                        r.source_listing_id,
                        r.url,
                        r.title,
                        r.employer,
                        r.agency,
                        r.location_raw,
                        _dt_str(r.posted_at),
                        r.salary_raw,
                        r.contract_type_raw,
                        r.description_raw,
                        json.dumps(r.metadata) if r.metadata else None,
                        _dt_str(r.fetched_at),
                    ),
                )
                inserted += cur.rowcount
        logger.debug("insert_raw: %d new row(s) from %d input(s)", inserted, len(raws))
        return inserted

    # ------------------------------------------------------------------
    # Insert normalised
    # ------------------------------------------------------------------

    def insert_normalized(self, listings: list[NormalizedListing]) -> int:
        """Upsert normalised listings (INSERT OR REPLACE updates last_seen_at).

        Returns the count of inserted/replaced rows.
        """
        inserted = 0
        with self._conn:
            for n in listings:
                cur = self._conn.execute(
                    """
                    INSERT OR REPLACE INTO normalized_listings
                      (listing_id, source, source_listing_id, source_url,
                       source_urls_json, title, employer, agency,
                       location, location_normalized, country,
                       posted_at, start_date_raw, start_date, asap_flag,
                       duration_raw, duration_weeks,
                       day_rate_min, day_rate_max, rate_currency, rate_period,
                       ir35_status, contract_type, remote_policy, sector,
                       description_raw, description_clean,
                       discovered_at, last_seen_at,
                       is_new_listing, sanity_flags_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        n.listing_id,
                        n.source,
                        n.source_listing_id,
                        n.source_url,
                        json.dumps(n.source_urls),
                        n.title,
                        n.employer,
                        n.agency,
                        n.location,
                        n.location_normalized,
                        n.country,
                        _dt_str(n.posted_at),
                        n.start_date_raw,
                        n.start_date.isoformat() if n.start_date else None,
                        int(n.asap_flag),
                        n.duration_raw,
                        n.duration_weeks,
                        n.day_rate_min,
                        n.day_rate_max,
                        n.rate_currency,
                        n.rate_period,
                        n.ir35_status,
                        n.contract_type,
                        n.remote_policy,
                        n.sector,
                        n.description_raw,
                        n.description_clean,
                        _dt_str(n.discovered_at),
                        _dt_str(n.last_seen_at),
                        int(n.is_new_listing),
                        json.dumps(n.sanity_flags),
                    ),
                )
                inserted += cur.rowcount
        logger.debug("insert_normalized: %d row(s) upserted", inserted)
        return inserted

    # ------------------------------------------------------------------
    # Insert dedup groups
    # ------------------------------------------------------------------

    def insert_dedup_groups(self, groups: dict[str, list[str]]) -> None:
        """Persist dedup group memberships (INSERT OR IGNORE on collision)."""
        with self._conn:
            for group_id, members in groups.items():
                for mid in members:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO dedup_groups "
                        "(group_id, member_listing_id) VALUES (?,?)",
                        (group_id, mid),
                    )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent_normalized(self, within_days: int = 7) -> list[NormalizedListing]:
        """Return normalised listings discovered within the last *within_days* days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
        rows = self._conn.execute(
            "SELECT * FROM normalized_listings "
            "WHERE discovered_at >= ? ORDER BY discovered_at DESC",
            (_dt_str(cutoff),),
        ).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def get_for_report(
        self,
        date_range: tuple[date, date],
    ) -> list[NormalizedListing]:
        """Return normalised listings whose posted_at falls within *date_range*."""
        start, end = date_range
        rows = self._conn.execute(
            """
            SELECT * FROM normalized_listings
            WHERE posted_at >= ? AND posted_at <= ?
            ORDER BY posted_at DESC
            """,
            (start.isoformat(), end.isoformat() + "T23:59:59Z"),
        ).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _dt_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _row_to_normalized(row: sqlite3.Row) -> NormalizedListing:
    d: dict[str, Any] = dict(row)
    return NormalizedListing(
        listing_id=d["listing_id"],
        source=d["source"],
        source_listing_id=d["source_listing_id"],
        source_url=d.get("source_url", ""),
        source_urls=json.loads(d.get("source_urls_json") or "[]"),
        title=d["title"],
        employer=d.get("employer"),
        agency=d.get("agency"),
        location=d.get("location") or "",
        location_normalized=d.get("location_normalized") or "",
        country=d.get("country") or "GB",
        posted_at=_parse_dt(d.get("posted_at")),
        start_date_raw=d.get("start_date_raw"),
        start_date=_parse_date(d.get("start_date")),
        asap_flag=bool(d.get("asap_flag", 0)),
        duration_raw=d.get("duration_raw"),
        duration_weeks=d.get("duration_weeks"),
        day_rate_min=d.get("day_rate_min"),
        day_rate_max=d.get("day_rate_max"),
        rate_currency=d.get("rate_currency") or "GBP",
        rate_period=d.get("rate_period"),
        ir35_status=d.get("ir35_status"),
        contract_type=d.get("contract_type") or "contract",
        remote_policy=d.get("remote_policy"),
        sector=d.get("sector") or "generalist",
        description_raw=d.get("description_raw"),
        description_clean=d.get("description_clean"),
        discovered_at=_parse_dt(d["discovered_at"]) or datetime.now(timezone.utc),
        last_seen_at=_parse_dt(d["last_seen_at"]) or datetime.now(timezone.utc),
        is_new_listing=bool(d.get("is_new_listing", 0)),
        sanity_flags=json.loads(d.get("sanity_flags_json") or "[]"),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path as P

    # Allow running as: python src/mechpm/storage/sqlite.py  (from repo root)
    sys.path.insert(0, str(P(__file__).parent.parent.parent.parent))

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s — %(message)s",
    )

    from mechpm.adapters.base import RawListing as RL
    from mechpm.extractor.pipeline import extract
    from mechpm.extractor.dedup import dedupe_with_groups
    from mechpm.extractor.filters import passes_all

    print("=" * 60)
    print("mechpm storage smoke test  (2026-06-12)")
    print("=" * 60)

    repo = Repo(":memory:")
    print("[OK] DB created in :memory:")

    # Two similar listings from different sources
    raw1 = RL(
        source="reed",
        source_listing_id="SMOKE-001",
        url="https://www.reed.co.uk/jobs/smoke-001",
        title="Senior Project Manager – Mechanical Engineering",
        employer="Siemens Energy",
        location_raw="Manchester, M1 1AA",
        posted_at=datetime.now(timezone.utc),
        contract_type_raw="contract",
        salary_raw="£550 - £650 per day",
        description_raw=(
            "We are seeking a Senior Project Manager with experience in HVAC "
            "and mechanical systems. Outside IR35. Start ASAP. 6 months duration. "
            "You will manage gantt charts, risk registers, and stakeholder plans."
        ),
    )
    raw2 = RL(
        source="totaljobs",
        source_listing_id="SMOKE-002",
        url="https://www.totaljobs.com/job/smoke-002",
        title="Senior Project Manager – Mechanical Engineering",
        employer="Siemens Energy Ltd",
        location_raw="Manchester",
        posted_at=datetime.now(timezone.utc),
        contract_type_raw="Contract",
        salary_raw="£580/day",
        description_raw=(
            "Senior PM role for HVAC and M&E projects. Outside IR35. "
            "Immediate start. Manage project plans, milestones, change control."
        ),
    )

    inserted_raw = repo.insert_raw([raw1, raw2])
    print(f"[OK] Inserted {inserted_raw} raw listing(s)")

    norm1 = extract(raw1)
    norm2 = extract(raw2)

    print(f"\nExtracted listings:")
    for n in [norm1, norm2]:
        print(
            f"  [{n.listing_id}] '{n.title}' | sector={n.sector} | "
            f"rate=£{n.day_rate_min}–{n.day_rate_max}/{n.rate_period} | "
            f"IR35={n.ir35_status} | asap={n.asap_flag} | "
            f"dur_weeks={n.duration_weeks} | country={n.country}"
        )

    # Filters
    for n in [norm1, norm2]:
        ok, failures = passes_all(n)
        status = "[OK] PASS" if ok else f"[FAIL] FAIL {failures}"
        print(f"  filter({n.listing_id}): {status}")

    # Dedup
    result = dedupe_with_groups([norm1, norm2])
    print(
        f"\nAfter dedup: {len(result.listings)} canonical listing(s) "
        f"(from 2 input(s))"
    )
    if result.listings:
        c = result.listings[0]
        print(f"  Canonical: '{c.title}' | sources={c.source_urls}")
    print(f"  Dedup groups: {result.groups}")

    inserted_norm = repo.insert_normalized(result.listings)
    repo.insert_dedup_groups(result.groups)
    print(f"\n[OK] Inserted {inserted_norm} normalised listing(s)")

    queried = repo.get_recent_normalized(within_days=1)
    print(f"[OK] Queried back {len(queried)} listing(s) from DB")
    for r in queried:
        print(
            f"  [OK] {r.listing_id}: '{r.title}' | {r.sector} | "
            f"£{r.day_rate_min}–{r.day_rate_max} | IR35={r.ir35_status}"
        )

    print("\n[ALL OK]  Smoke test passed")
