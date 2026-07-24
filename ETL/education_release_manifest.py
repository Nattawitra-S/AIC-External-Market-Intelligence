"""
ETL/education_release_manifest.py
===================================
Records publication metadata (release label/year/month, source URLs,
sizes, SHA-256 hashes) alongside the downloaded Basic/Detailed workbooks.

Why this exists: Pivot_Basic_All_web.xlsx / Pivot_Detailed_Latest_web.xlsx
are YTD-cumulative and contain many historical years/months in every file,
regardless of which monthly release published them -- so scanning file
CONTENTS cannot reliably identify which government publication release a
local file came from. That has to be recorded at download time instead,
in a small sidecar JSON file next to the canonical input workbooks.

The manifest is runtime data (lives under raw_data/, already gitignored by
the blanket `raw_data/` rule) -- this module defines its schema and
read/write/validate behaviour, not any actual manifest content.

Manifest schema (see ReleaseManifest / WorkbookRecord below):
{
  "status": "downloaded",   -- or "extracted", see lifecycle note below
  "release_label": "May 2026",
  "release_year": 2026,
  "release_month": 5,
  "publication_page_url": "https://www.education.gov.au/...",
  "downloaded_at_utc": "2026-07-23T06:41:03Z",
  "basic":    {"filename": "Pivot_Basic_All_web.xlsx", "url": "...", "size_bytes": 123, "sha256": "...",
               "release_year": 2026, "release_month": 5,
               "raw_split_filename": null, "raw_split_size_bytes": null, "raw_split_sha256": null},
  "detailed": {"filename": "Pivot_Detailed_Latest_web.xlsx", "url": "...", "size_bytes": 456, "sha256": "...",
               "release_year": 2026, "release_month": 5,
               "raw_split_filename": null, "raw_split_size_bytes": null, "raw_split_sha256": null}
}

Per-entry release_year/release_month are a defence-in-depth addition
beyond the minimum required fields: they let validation catch a
manifest that has been hand-edited or corrupted into internal
inconsistency (Basic and Detailed disagreeing about which release they
belong to), not just a mismatch against the top-level fields.

Lifecycle (status field): the manifest is written in two stages, because
downloading the input workbooks and extracting them into raw-split
outputs are two separate operations that can fail independently:

  1. "downloaded" -- written by download_and_validate_release_pair() right
     after both input workbooks pass validation. raw_split_* fields are
     still null; nothing is known yet about the raw-split outputs.
  2. "extracted"  -- written by mark_extracted() (called from
     etl_education_v2.download_and_extract_release_pair(), AFTER the
     extractor has run and _run_extractor_pair() has verified both
     raw-split outputs are fresh/non-empty). This fills in raw_split_
     filename/size_bytes/sha256 for both entries.

If extraction fails or is never run after a successful download, the
manifest simply stays at "downloaded" -- this is what lets --local-only
detect "new inputs were downloaded, but extraction never completed for
them" and refuse to parse whatever (possibly stale, from an OLDER
release) raw-split output happens to still be sitting on disk. Without
this, a later --local-only run could validate the new inputs against the
manifest yet still silently parse old raw-split outputs left over from
extracting a previous release.

Deliberately contains no credentials, no local absolute paths, and no
secrets -- only bare filenames, public URLs, sizes, and hashes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MANIFEST_FILENAME = "education_release_manifest.json"

_MONTH_NUM_TO_NAME = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def month_label(year: int, month: int) -> str:
    """e.g. (2026, 5) -> 'May 2026'."""
    return f"{_MONTH_NUM_TO_NAME[month]} {year}"


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


STATUS_DOWNLOADED = "downloaded"
STATUS_EXTRACTED = "extracted"


@dataclass(frozen=True)
class WorkbookRecord:
    filename: str
    url: str
    size_bytes: int
    sha256: str
    release_year: int
    release_month: int
    # Populated only once status == STATUS_EXTRACTED (see module docstring).
    raw_split_filename: Optional[str] = None
    raw_split_size_bytes: Optional[int] = None
    raw_split_sha256: Optional[str] = None


@dataclass(frozen=True)
class ReleaseManifest:
    release_label: str
    release_year: int
    release_month: int
    publication_page_url: str
    downloaded_at_utc: str
    basic: WorkbookRecord
    detailed: WorkbookRecord
    status: str = STATUS_DOWNLOADED

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ReleaseManifest":
        return ReleaseManifest(
            release_label=d["release_label"],
            release_year=d["release_year"],
            release_month=d["release_month"],
            publication_page_url=d["publication_page_url"],
            downloaded_at_utc=d["downloaded_at_utc"],
            basic=WorkbookRecord(**d["basic"]),
            detailed=WorkbookRecord(**d["detailed"]),
            status=d.get("status", STATUS_DOWNLOADED),
        )


def build_manifest(
    *,
    release_year: int,
    release_month: int,
    publication_page_url: str,
    basic_filename: str,
    basic_url: str,
    basic_path: Path,
    detailed_filename: str,
    detailed_url: str,
    detailed_path: Path,
) -> ReleaseManifest:
    """
    Build a manifest from already-downloaded-and-validated workbook paths.
    Both workbook entries always share the same release_year/release_month
    -- there is no code path that can construct a manifest with the two
    disagreeing (only manual/corrupted edits could produce that, which is
    exactly what validate_local_files_against_manifest() below guards
    against).
    """
    return ReleaseManifest(
        release_label=month_label(release_year, release_month),
        release_year=release_year,
        release_month=release_month,
        publication_page_url=publication_page_url,
        downloaded_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status=STATUS_DOWNLOADED,
        basic=WorkbookRecord(
            filename=basic_filename,
            url=basic_url,
            size_bytes=basic_path.stat().st_size,
            sha256=compute_sha256(basic_path),
            release_year=release_year,
            release_month=release_month,
        ),
        detailed=WorkbookRecord(
            filename=detailed_filename,
            url=detailed_url,
            size_bytes=detailed_path.stat().st_size,
            sha256=compute_sha256(detailed_path),
            release_year=release_year,
            release_month=release_month,
        ),
    )


def mark_extracted(
    manifest: ReleaseManifest,
    basic_raw_split: Path,
    detailed_raw_split: Path,
) -> ReleaseManifest:
    """
    Return a NEW ReleaseManifest with status="extracted" and the raw_split_*
    fields populated for both entries, based on the just-verified-fresh
    raw-split output files. Does not mutate `manifest` (it's frozen) or
    write anything -- the caller (etl_education_v2._mark_manifest_extracted)
    is responsible for persisting the result.
    """
    return ReleaseManifest(
        release_label=manifest.release_label,
        release_year=manifest.release_year,
        release_month=manifest.release_month,
        publication_page_url=manifest.publication_page_url,
        downloaded_at_utc=manifest.downloaded_at_utc,
        status=STATUS_EXTRACTED,
        basic=WorkbookRecord(
            filename=manifest.basic.filename,
            url=manifest.basic.url,
            size_bytes=manifest.basic.size_bytes,
            sha256=manifest.basic.sha256,
            release_year=manifest.basic.release_year,
            release_month=manifest.basic.release_month,
            raw_split_filename=basic_raw_split.name,
            raw_split_size_bytes=basic_raw_split.stat().st_size,
            raw_split_sha256=compute_sha256(basic_raw_split),
        ),
        detailed=WorkbookRecord(
            filename=manifest.detailed.filename,
            url=manifest.detailed.url,
            size_bytes=manifest.detailed.size_bytes,
            sha256=manifest.detailed.sha256,
            release_year=manifest.detailed.release_year,
            release_month=manifest.detailed.release_month,
            raw_split_filename=detailed_raw_split.name,
            raw_split_size_bytes=detailed_raw_split.stat().st_size,
            raw_split_sha256=compute_sha256(detailed_raw_split),
        ),
    )


def write_manifest(manifest: ReleaseManifest, dest_path: Path) -> None:
    """Write the manifest as indented JSON to `dest_path` (plain write --
    callers responsible for their own atomic-replace strategy if writing
    directly over a live file; the production download flow writes to a
    .part path and replaces it as part of a larger multi-file swap)."""
    dest_path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")


def read_manifest(path: Path) -> Optional[ReleaseManifest]:
    """Return None if no manifest file exists at all (legacy local files
    that predate this feature) -- that is NOT an error by itself; callers
    decide what to do based on mode (see verify_local_only_release_manifest
    in etl_education_v2.py)."""
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return ReleaseManifest.from_dict(data)


class ManifestValidationError(Exception):
    """Raised when local files don't match what the manifest recorded."""


def validate_local_files_against_manifest(
    manifest: ReleaseManifest,
    basic_path: Path,
    detailed_path: Path,
) -> None:
    """
    Verify the actual local Basic/Detailed input files match what the
    manifest recorded: filenames, per-entry release year/month consistency
    (both with each other and with the manifest's top-level release),
    existence, size, and SHA-256. Raises ManifestValidationError with a
    clear message on the first mismatch found.
    """
    if manifest.basic.filename != basic_path.name:
        raise ManifestValidationError(
            f"Manifest Basic filename ({manifest.basic.filename!r}) does not match "
            f"the expected local filename ({basic_path.name!r})"
        )
    if manifest.detailed.filename != detailed_path.name:
        raise ManifestValidationError(
            f"Manifest Detailed filename ({manifest.detailed.filename!r}) does not match "
            f"the expected local filename ({detailed_path.name!r})"
        )

    # Basic/Detailed release mismatch: both per-entry release fields must
    # agree with each other AND with the manifest's top-level release.
    periods = {
        "manifest": (manifest.release_year, manifest.release_month),
        "basic": (manifest.basic.release_year, manifest.basic.release_month),
        "detailed": (manifest.detailed.release_year, manifest.detailed.release_month),
    }
    if len(set(periods.values())) > 1:
        raise ManifestValidationError(
            f"Basic/Detailed release mismatch in manifest: {periods} -- "
            "Basic and Detailed must belong to the same reporting month."
        )

    for label, path, record in (
        ("Basic", basic_path, manifest.basic),
        ("Detailed", detailed_path, manifest.detailed),
    ):
        if not path.exists():
            raise ManifestValidationError(f"{label} workbook is missing: {path}")
        actual_size = path.stat().st_size
        if actual_size != record.size_bytes:
            raise ManifestValidationError(
                f"{label} workbook size mismatch: manifest says {record.size_bytes:,} bytes, "
                f"actual is {actual_size:,} bytes ({path})"
            )
        actual_sha256 = compute_sha256(path)
        if actual_sha256 != record.sha256:
            raise ManifestValidationError(
                f"{label} workbook SHA-256 mismatch (file has been modified or "
                f"replaced since the manifest was written): {path}"
            )


def validate_raw_split_outputs_against_manifest(
    manifest: ReleaseManifest,
    basic_raw_split: Path,
    detailed_raw_split: Path,
) -> None:
    """
    Verify the raw-split OUTPUT files match what the manifest recorded
    from the last successful extraction (filenames, existence, size,
    SHA-256). Only meaningful once manifest.status == STATUS_EXTRACTED --
    callers must check that first (see
    etl_education_v2.verify_local_only_release_manifest), since the
    raw_split_* fields are null while status == STATUS_DOWNLOADED.
    """
    for label, path, record in (
        ("Basic", basic_raw_split, manifest.basic),
        ("Detailed", detailed_raw_split, manifest.detailed),
    ):
        if record.raw_split_filename is None:
            raise ManifestValidationError(
                f"Manifest has no recorded raw-split info for {label} despite "
                f"status={manifest.status!r} -- corrupted or hand-edited manifest"
            )
        if record.raw_split_filename != path.name:
            raise ManifestValidationError(
                f"Manifest {label} raw-split filename ({record.raw_split_filename!r}) "
                f"does not match the expected filename ({path.name!r})"
            )
        if not path.exists():
            raise ManifestValidationError(f"{label} raw-split output is missing: {path}")
        actual_size = path.stat().st_size
        if actual_size != record.raw_split_size_bytes:
            raise ManifestValidationError(
                f"{label} raw-split output size mismatch: manifest says "
                f"{record.raw_split_size_bytes:,} bytes, actual is {actual_size:,} bytes ({path})"
            )
        actual_sha256 = compute_sha256(path)
        if actual_sha256 != record.raw_split_sha256:
            raise ManifestValidationError(
                f"{label} raw-split output SHA-256 mismatch (the extracted output has "
                f"changed since the manifest recorded it -- possibly stale from a "
                f"different extraction): {path}"
            )
