"""
etl_education_v2.py
====================
ETL: Department of Education — International Student Data

API:  Basic/Detailed download URLs are auto-discovered from the public
      publication page each run (see ETL/education_discovery.py) -- the
      URLs embed a resource ID that changes with every monthly release,
      so they are never hardcoded. SOURCES[...]["url"] below is a stale
      literal, kept only as a fallback for the legacy SQLite run().
      Fallback: local XLSX files in raw_data/department_of_education/

Datasets:
  1. Pivot_Basic_All_web.xlsx      — YTD enrolments/commencements by nationality/sector/state
  2. Pivot_Detailed_Latest_web.xlsx — Finer grain than Basic: adds region, broad/
     narrow/detailed field of education, level of study, and foundation status.
     NOT the same grain as Basic (empirically verified: 94.7% of Detailed rows
     collide on Basic's key) -- loads into a separate destination table,
     fact_student_enrolment_detailed, and must never be unioned or summed
     together with Basic in the same aggregation.
  3. International students 2005-2025.xlsx — Historical annual data
  4. SA4 enrolments by SA4/remoteness/field — spatial breakdown

Tables:
  • education_enrolments                (detailed current-year pivot)
  • education_int_students_historical   (2005-2025 annual)
  • education_sa4_enrolments            (SA4 spatial breakdown)

USAGE:
    python ETL/etl_education_v2.py
    python ETL/etl_education_v2.py --force   # force re-download
    python ETL/etl_education_v2.py --local-only
"""

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

from ETL.lib_etl import (
    add_etl_meta, download_file, get_db, get_logger, norm_col, upsert_df,
)

log = get_logger("ETL_EDUCATION")

_MONTH_NAME_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _month_name_to_num(v):
    return _MONTH_NAME_TO_NUM.get(str(v).strip().lower())


BASE_DIR = Path(__file__).parent.parent
RAW_DIR  = BASE_DIR / "raw_data" / "department_of_education"
DB_PATH  = BASE_DIR / "data" / "aic_occupation_intelligence.db"
SCHEMA   = Path(__file__).parent / "schema.sql"

# ── Direct download URLs (stable) ─────────────────────────────────────────────
# These are the permanent data download links from education.gov.au
SOURCES = [
    {
        "url":      "https://www.education.gov.au/download/20217/international-student-data-year-date-ytd/45085/may-2026-all-data/xlsx",
        "filename": "Pivot_Basic_All_web_latest.xlsx",
        "desc":     "Basic Pivot — YTD enrolments/commencements",
        "table":    "education_enrolments",
        "parser":   "parse_pivot_basic",
    },
    {
        "url":      "https://www.education.gov.au/sites/default/files/documents/Pivot_Detailed_Latest_web.xlsx",
        "filename": "Pivot_Detailed_Latest_web_latest.xlsx",
        "desc":     "Detailed Pivot — extended breakdown",
        "table":    "education_enrolments",
        "parser":   "parse_pivot_detailed",
    },
    {
        "url":      None,  # Historical file — no public direct URL, use local
        "filename": None,
        "desc":     "International students 2005-2025 (historical)",
        "table":    "education_int_students_historical",
        "parser":   "parse_historical",
        "local_glob": "International students studying in Australia*.xlsx",
    },
    {
        "url":      None,
        "filename": None,
        "desc":     "SA4 enrolments (spatial)",
        "table":    "education_sa4_enrolments",
        "parser":   "parse_sa4",
        "local_glob": "SA4_International Student Enrolments*.xlsx",
    },
]

# ── Fallback local files ───────────────────────────────────────────────────────
# Pivot_Basic_All_web.xlsx from the website is a true Excel pivot (merged cells, no tabular data).
# It must be run through the File_extractor 2 pipeline to produce a flat/tabular
# file before parse_pivot_basic() can read it.
#
# The extractor's data directories (input/output) live under
# raw_data/department_of_education/File_extractor 2/ (gitignored). The
# extractor SOURCE CODE is tracked separately at ETL/tools/extract_fixed.py
# — it is not colocated with its data.
FILE_EXTRACTOR_DIR   = BASE_DIR / "raw_data" / "department_of_education" / "File_extractor 2"
EXTRACTOR_SCRIPT      = BASE_DIR / "ETL" / "tools" / "extract_fixed.py"
EXTRACTOR_INPUT_DIR   = FILE_EXTRACTOR_DIR / "input"
EXTRACTOR_OUTPUT_DIR  = FILE_EXTRACTOR_DIR / "output"
PIVOT_BASIC_INPUT     = EXTRACTOR_INPUT_DIR / "Pivot_Basic_All_web.xlsx"
PIVOT_DETAILED_INPUT  = EXTRACTOR_INPUT_DIR / "Pivot_Detailed_Latest_web.xlsx"
# These are the ONLY valid Education Basic/Detailed outputs. Their sibling
# summary workbooks (Pivot_*_extracted.xlsx, no "_raw_split" suffix) do not
# contain every raw row and must never be selected here.
PIVOT_BASIC_RAW_SPLIT    = EXTRACTOR_OUTPUT_DIR / "Pivot_Basic_All_web_extracted_raw_split.xlsx"
PIVOT_DETAILED_RAW_SPLIT = EXTRACTOR_OUTPUT_DIR / "Pivot_Detailed_Latest_web_extracted_raw_split.xlsx"


def _find_extracted_pivot_basic() -> Path | None:
    """
    Return the canonical raw-split output Path if it exists and is non-empty,
    else None. Deterministic — no glob, no "most recent/alphabetically last"
    guessing between multiple candidate files.
    """
    if PIVOT_BASIC_RAW_SPLIT.exists() and PIVOT_BASIC_RAW_SPLIT.stat().st_size > 0:
        return PIVOT_BASIC_RAW_SPLIT
    return None


def _find_extracted_pivot_detailed() -> Path | None:
    """Detailed counterpart of _find_extracted_pivot_basic() -- same
    deterministic, no-glob guarantee."""
    if PIVOT_DETAILED_RAW_SPLIT.exists() and PIVOT_DETAILED_RAW_SPLIT.stat().st_size > 0:
        return PIVOT_DETAILED_RAW_SPLIT
    return None


def _validate_pivot_cache_xlsx(path: Path) -> None:
    """
    Validate that `path` is a genuine XLSX pivot-cache workbook before it is
    trusted enough to replace a canonical input file. Catches the failure
    mode of a government error/redirect page being saved with an .xlsx
    extension: an HTML error page is neither large enough, nor has a ZIP
    signature, nor contains pivot cache parts, so it is rejected at every
    one of these checks rather than silently accepted.
    """
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"Downloaded file is missing or empty: {path}")
    if path.stat().st_size < 10_000:
        raise ValueError(
            f"Downloaded file is implausibly small ({path.stat().st_size} bytes) for a "
            f"pivot-cache workbook -- likely an error page, not real data: {path}"
        )

    with open(path, "rb") as f:
        magic = f.read(4)
    if magic[:2] != b"PK":
        raise ValueError(
            f"Downloaded file does not have a valid ZIP/XLSX signature "
            f"(got {magic!r}) -- likely an HTML error page saved as .xlsx: {path}"
        )

    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile as e:
        raise ValueError(f"Downloaded file is not a valid ZIP/XLSX archive: {path}") from e

    has_defn = any("pivotCacheDefinition" in n and not n.endswith(".rels") for n in names)
    has_records = any("pivotCacheRecords" in n and not n.endswith(".rels") for n in names)
    if not (has_defn and has_records):
        raise ValueError(
            f"Downloaded file does not contain pivot cache definition/records "
            f"-- not a valid pivot workbook: {path}"
        )


def _atomic_replace_release_files(triples: list[tuple[Path, Path]]) -> None:
    """
    Replace multiple "live" files with their validated "new" counterparts
    as one rollback-protected operation (here: Basic input, Detailed
    input, and the release manifest). Existing live files are first moved
    aside to `<name>.bak` (not deleted or overwritten yet), then every new
    file is moved into place. If ANY of those final moves fails, every
    live path with a `.bak` is restored from it. Live paths that had no
    prior file (first-ever run, nothing to back up) but were already
    installed by this call are removed instead, since there is nothing
    to restore them to. Either way, the pipeline never ends up with a
    new Basic paired with an old Detailed, or a manifest that doesn't
    match what's actually on disk.
    On success, the `.bak` files are removed.
    """
    backups: list[tuple[Path, Path]] = []  # (live_path, backup_path)
    installed: list[Path] = []  # live paths this call has moved a new file into
    try:
        for _, live in triples:
            if live.exists():
                backup = live.with_name(live.name + ".bak")
                live.replace(backup)
                backups.append((live, backup))

        for new, live in triples:
            new.replace(live)
            installed.append(live)

    except Exception:
        backed_up_live_paths = {live for live, _ in backups}
        for live, backup in backups:
            if backup.exists():
                backup.replace(live)
        # Any live path this call installed that had no prior backup was
        # created fresh by this call (e.g. first-ever run) -- there is
        # nothing to restore it to, so it must be removed rather than
        # left behind as an orphaned partial install.
        for live in installed:
            if live not in backed_up_live_paths:
                live.unlink(missing_ok=True)
        raise
    else:
        for _, backup in backups:
            backup.unlink(missing_ok=True)


def download_and_validate_release_pair(release) -> tuple[Path, Path]:
    """
    Download both Basic and Detailed workbooks to temporary .part files,
    validate each as a genuine pivot-cache XLSX, build a release manifest
    recording their publication metadata (release label/year/month,
    source URLs, sizes, SHA-256 hashes), and only if everything passes
    replace the canonical input files AND the manifest -- atomically, as
    one 3-file rollback-protected unit (see _atomic_replace_release_files).

    If any download, validation, or replacement step fails, NONE of the
    three live files are left in a mismatched state: this never leaves
    Basic updated while Detailed is stale (or vice versa), and never
    leaves a manifest that doesn't match what's actually on disk. Leftover
    .part files are always cleaned up on failure.

    The manifest exists because the workbooks themselves are YTD-cumulative
    and contain many historical years/months in every release -- their
    CONTENTS cannot reveal which monthly publication produced them, so
    that has to be recorded at download time (see
    ETL/education_release_manifest.py).
    """
    from ETL.education_discovery import PUBLICATION_PAGE_URL
    from ETL.education_release_manifest import MANIFEST_FILENAME, build_manifest, month_label, write_manifest

    basic_part_name = PIVOT_BASIC_INPUT.name + ".part"
    detailed_part_name = PIVOT_DETAILED_INPUT.name + ".part"
    manifest_live = EXTRACTOR_INPUT_DIR / MANIFEST_FILENAME
    manifest_part = EXTRACTOR_INPUT_DIR / (MANIFEST_FILENAME + ".part")

    basic_part = EXTRACTOR_INPUT_DIR / basic_part_name
    detailed_part = EXTRACTOR_INPUT_DIR / detailed_part_name

    try:
        basic_part = download_file(release.basic_url, EXTRACTOR_INPUT_DIR, basic_part_name, force=True)
        _validate_pivot_cache_xlsx(basic_part)

        detailed_part = download_file(release.detailed_url, EXTRACTOR_INPUT_DIR, detailed_part_name, force=True)
        _validate_pivot_cache_xlsx(detailed_part)

        # Manifest is only built/written after BOTH workbooks passed validation.
        manifest = build_manifest(
            release_year=release.year,
            release_month=release.month,
            publication_page_url=PUBLICATION_PAGE_URL,
            basic_filename=PIVOT_BASIC_INPUT.name,
            basic_url=release.basic_url,
            basic_path=basic_part,
            detailed_filename=PIVOT_DETAILED_INPUT.name,
            detailed_url=release.detailed_url,
            detailed_path=detailed_part,
        )
        write_manifest(manifest, manifest_part)

        # All three validated -- replace live Basic, Detailed, and manifest
        # together as one rollback-protected unit.
        _atomic_replace_release_files([
            (basic_part, PIVOT_BASIC_INPUT),
            (detailed_part, PIVOT_DETAILED_INPUT),
            (manifest_part, manifest_live),
        ])
    except Exception:
        basic_part.unlink(missing_ok=True)
        detailed_part.unlink(missing_ok=True)
        manifest_part.unlink(missing_ok=True)
        raise

    log.info(
        f"  [Edu] Downloaded, validated, and recorded release manifest "
        f"({month_label(release.year, release.month)}) → "
        f"{PIVOT_BASIC_INPUT.name}, {PIVOT_DETAILED_INPUT.name}"
    )
    return PIVOT_BASIC_INPUT, PIVOT_DETAILED_INPUT


def verify_local_only_release_manifest(dry_run: bool) -> None:
    """
    Verify the release manifest for a --local-only run, without any
    network access. Raises RuntimeError on any mismatch (wrong filename,
    Basic/Detailed release inconsistency, missing file, size/hash
    mismatch) -- always fatal, in both --dry-run and real runs, since a
    mismatch here means the local files are provably NOT what they claim
    to be.

    If no manifest exists at all (the local files predate this feature),
    that is treated differently depending on mode:
      - --dry-run: allowed, with a prominent warning that these are
        UNVERIFIED LEGACY FILES whose release month cannot be confirmed --
        sufficient for path/routing checks only.
      - a real, MySQL-writing run: fatal. There is deliberately no
        override implemented yet; re-run the normal automated download
        (without --local-only) to produce a verified manifest.
    """
    from ETL.education_release_manifest import (
        MANIFEST_FILENAME, ManifestValidationError, read_manifest,
        validate_local_files_against_manifest,
    )

    manifest_path = EXTRACTOR_INPUT_DIR / MANIFEST_FILENAME
    manifest = read_manifest(manifest_path)

    if manifest is None:
        msg = (
            f"  [Edu] ⚠ UNVERIFIED LEGACY LOCAL FILES: no release manifest found at "
            f"{manifest_path} -- {PIVOT_BASIC_INPUT.name} / {PIVOT_DETAILED_INPUT.name} "
            "predate the release-manifest feature and their release month cannot be "
            "confirmed to match."
        )
        if dry_run:
            log.warning(msg + " Proceeding for --dry-run path/routing checks only.")
            return
        raise RuntimeError(
            msg + " Refusing to proceed with a real MySQL-writing --local-only run -- "
            "no override is currently implemented. Re-run the normal automated download "
            "(without --local-only) to create a verified manifest."
        )

    try:
        validate_local_files_against_manifest(manifest, PIVOT_BASIC_INPUT, PIVOT_DETAILED_INPUT)
    except ManifestValidationError as e:
        raise RuntimeError(f"  [Edu] --local-only release manifest validation failed: {e}") from e

    # The manifest existing and the INPUT files matching it only proves the
    # inputs were downloaded correctly -- it says nothing about whether
    # extraction ever completed for THIS input version. If extraction
    # failed or was never run, the manifest stays at status="downloaded"
    # and the current raw-split outputs could be stale leftovers from a
    # DIFFERENT (older) release. Both --dry-run and a real run would go on
    # to parse those raw-split files, so this must be fatal in both modes.
    from ETL.education_release_manifest import STATUS_EXTRACTED, validate_raw_split_outputs_against_manifest

    if manifest.status != STATUS_EXTRACTED:
        raise RuntimeError(
            f"  [Edu] --local-only: release manifest status is {manifest.status!r}, not "
            f"{STATUS_EXTRACTED!r} -- extraction did not complete for this downloaded input "
            "version, so the current raw-split output files cannot be trusted to correspond "
            "to it. Refusing to parse potentially stale raw-split outputs. Re-run the normal "
            "automated download+extract (without --local-only)."
        )

    try:
        validate_raw_split_outputs_against_manifest(manifest, PIVOT_BASIC_RAW_SPLIT, PIVOT_DETAILED_RAW_SPLIT)
    except ManifestValidationError as e:
        raise RuntimeError(f"  [Edu] --local-only raw-split output validation failed: {e}") from e

    log.info(
        f"  [Edu] --local-only: release manifest verified, including raw-split outputs "
        f"({manifest.release_label})"
    )


def _mark_manifest_extracted(basic_raw_split: Path, detailed_raw_split: Path) -> None:
    """
    Update the release manifest to status="extracted", recording each
    raw-split output's size/hash. This is the ONLY thing that proves
    extraction actually completed for the CURRENTLY downloaded inputs --
    if this is never called (extraction failed, or the process crashed
    before reaching here), the manifest stays at status="downloaded", and
    verify_local_only_release_manifest() will correctly refuse to trust
    whatever raw-split outputs happen to be sitting on disk.

    This is a single-file update performed via write-to-.part-then-replace
    (atomic), separate from the 3-file input+manifest swap in
    download_and_validate_release_pair() -- by the time this runs, the
    inputs are already safely committed, so only the manifest itself needs
    updating.
    """
    from ETL.education_release_manifest import MANIFEST_FILENAME, mark_extracted, read_manifest, write_manifest

    manifest_path = EXTRACTOR_INPUT_DIR / MANIFEST_FILENAME
    manifest = read_manifest(manifest_path)
    if manifest is None:
        raise RuntimeError(
            f"Cannot mark extraction complete: no release manifest found at {manifest_path} "
            "-- this should not happen immediately after a successful download_and_validate_release_pair()."
        )

    updated = mark_extracted(manifest, basic_raw_split, detailed_raw_split)

    tmp_path = manifest_path.with_suffix(manifest_path.suffix + ".part")
    write_manifest(updated, tmp_path)
    tmp_path.replace(manifest_path)
    log.info(f"  [Edu] Release manifest marked extracted ({updated.release_label})")


def _run_extractor_pair() -> tuple[Path, Path]:
    """
    Invoke the tracked extractor once, in auto-discovery mode (no filename
    argument -- it processes whichever of the canonical Basic/Detailed
    input files are present), and verify BOTH raw-split outputs were
    freshly produced. Raises on any failure — callers must treat this as
    fatal, never falling back to a stale raw-split file.
    """
    basic_before = PIVOT_BASIC_RAW_SPLIT.stat().st_mtime if PIVOT_BASIC_RAW_SPLIT.exists() else None
    detailed_before = PIVOT_DETAILED_RAW_SPLIT.stat().st_mtime if PIVOT_DETAILED_RAW_SPLIT.exists() else None

    # No cwd needed: ETL/tools/extract_fixed.py resolves its own input/output
    # directories from its tracked location, not from the working directory.
    subprocess.run([sys.executable, str(EXTRACTOR_SCRIPT)], check=True)

    for label, path, before in (
        ("Basic", PIVOT_BASIC_RAW_SPLIT, basic_before),
        ("Detailed", PIVOT_DETAILED_RAW_SPLIT, detailed_before),
    ):
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"Extractor did not produce a valid {label} raw-split output: {path}")
        after = path.stat().st_mtime
        if before is not None and after <= before:
            raise RuntimeError(
                f"Extractor ran but {label} raw-split output was not updated (stale file): {path}"
            )

    return PIVOT_BASIC_RAW_SPLIT, PIVOT_DETAILED_RAW_SPLIT


def download_and_extract_release_pair(force: bool = False) -> tuple[Path, Path]:
    """
    Full automated pipeline for a normal production Education run:

        discover newest complete release (Basic + Detailed, same month)
        -> download + validate both workbooks atomically, write manifest
           with status="downloaded"
        -> run the tracked extractor once (auto mode, both files present)
        -> verify both raw-split outputs are fresh and non-empty
        -> mark the manifest status="extracted" with raw-split hashes

    Raises on any failure. No MySQL connection is involved at this stage —
    this only prepares local files; the caller is responsible for stopping
    the pipeline (and never attempting a load) if this raises. If
    extraction fails, the manifest is left at status="downloaded" --
    exactly what lets a later --local-only run detect that the current
    raw-split outputs were never verified against this input version (see
    verify_local_only_release_manifest / education_release_manifest.py).

    `force` is accepted for call-site compatibility but does not gate
    *whether* discovery/download happens — a normal run always re-checks
    for the newest release, so the caller never has to remember --force.
    """
    from ETL.education_discovery import discover_current_education_urls

    release = discover_current_education_urls()
    log.info(
        f"  [Edu] Discovered release {release.year}-{release.month:02d}: "
        f"Basic={release.basic_url}  Detailed={release.detailed_url}"
    )

    download_and_validate_release_pair(release)

    basic_raw_split, detailed_raw_split = _run_extractor_pair()
    _mark_manifest_extracted(basic_raw_split, detailed_raw_split)

    return basic_raw_split, detailed_raw_split


LOCAL_FILES = {
    "parse_pivot_basic":    _find_extracted_pivot_basic(),   # pre-extracted flat file
    "parse_pivot_detailed": _find_extracted_pivot_detailed(),
}


# ── PARSERS ───────────────────────────────────────────────────────────────────

def _find_header(raw: pd.DataFrame, hints: list[str]) -> int:
    for i, row in raw.head(15).iterrows():
        if row.astype(str).str.contains("|".join(hints), case=False, na=False, regex=True).any():
            return i
    return 0


def parse_pivot_basic(path: Path) -> pd.DataFrame:
    """
    Parse the pre-extracted flat version of Pivot_Basic_All_web.xlsx.

    The downloaded Pivot_Basic_All_web.xlsx from education.gov.au is a true
    Excel pivot table with merged cells — it is NOT tabular.  The expected
    input is the File_extractor 2 raw-split output:
        raw_data/department_of_education/File_extractor 2/output/Pivot_Basic_All_web_extracted_raw_split.xlsx
    which is already flat / tabular and may span multiple sheets.

    Expected columns (post-normalise):
        year, month, nationality, state, sector, new_to_australia,
        ends_this_year, data_ytd_enrolments, data_ytd_commencements,
        providertype / provider_type, total
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Sheets ({len(xl.sheet_names)}): {xl.sheet_names[:8]}")

    # Column name standardisation map
    rename = {
        "ytd_enrolments":         "data_ytd_enrolments",
        "enrolments":             "data_ytd_enrolments",
        "ytd_commencements":      "data_ytd_commencements",
        "commencements":          "data_ytd_commencements",
        "providertype":           "provider_type",
    }

    frames = []
    for sheet in xl.sheet_names:
        # Skip obvious non-data sheets
        if any(x in sheet.lower() for x in ["note", "content", "glossary", "source", "readme"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Year", "Month", "Nationality", "Sector", "State"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            if "year" not in df.columns:
                log.debug(f"  Sheet '{sheet}' — no 'year' column, skipping")
                continue

            df = df.rename(columns=rename)

            if "month" in df.columns:
                # Month is an abbreviated name ("Jul", "Jan"), not a number.
                # pd.to_numeric(..., errors="coerce") would silently turn
                # every value into NaN (which MySQL then forces to 0 on
                # insert into a NOT NULL column) -- map name -> number first.
                df["month"] = df["month"].map(_month_name_to_num)

            # Convert numeric columns
            for col in ["year", "month", "data_ytd_enrolments", "data_ytd_commencements", "total"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=[c for c in ["year", "month"] if c in df.columns])
            if df.empty:
                continue

            log.info(f"  Sheet '{sheet}': {len(df):,} rows")
            frames.append(df)
        except Exception as e:
            log.warning(f"  Sheet '{sheet}': {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in [
        "year", "month", "nationality", "state", "sector", "provider_type",
        "new_to_australia", "ends_this_year",
        "data_ytd_enrolments", "data_ytd_commencements", "total",
    ] if c in result.columns]
    return result[keep]


def parse_pivot_detailed(path: Path) -> pd.DataFrame:
    """
    Parse the pre-extracted flat version of Pivot_Detailed_Latest_web.xlsx.

    Detailed is NOT the same grain as Basic -- it subdivides every Basic-grain
    (year, month, nationality, state, sector, provider_type, new_to_australia,
    ends_this_year) combination further by region, field of education (broad/
    narrow/detailed), level of study, and foundation status. Empirically
    verified against the full 1,480,597-row dataset: 1,401,989 of those rows
    (94.7%) share their Basic-grain key with at least one other Detailed row
    -- so this MUST have its own parsing/keep-list and MUST NOT be routed
    through parse_pivot_basic()'s truncated column set or into the same
    destination table as Basic (see fact_student_enrolment_detailed).

    The source 'Total' column is NOT included in the output: verified
    against the full dataset, `total == data_ytd_enrolments` for 100.00% of
    rows (1,480,597/1,480,597) -- it is an exact duplicate, not an
    independent measure, so keeping it would just be redundant.

    Expected columns (post-normalise):
        year, month, region, nationality, state, provider_type, sector,
        broad_field_of_education, narrow_field_of_education,
        detailed_field_of_education, level_of_study, foundation,
        new_to_australia, ends_this_year, data_ytd_enrolments,
        data_ytd_commencements, data_as_at_1st_month,
        data_enrolments_for_month, data_commencements_for_month
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Sheets ({len(xl.sheet_names)}): {xl.sheet_names[:8]}")

    # Column name standardisation map. norm_col("ProviderType") -> "providertype"
    # (no separator character to split the words on) -- must be renamed
    # explicitly, exactly like parse_pivot_basic() does, or provider_type
    # silently disappears from the natural key.
    rename = {
        "ytd_enrolments":         "data_ytd_enrolments",
        "enrolments":             "data_ytd_enrolments",
        "ytd_commencements":      "data_ytd_commencements",
        "commencements":          "data_ytd_commencements",
        "providertype":           "provider_type",
        "as_at_1st_month":        "data_as_at_1st_month",
        "enrolments_for_month":   "data_enrolments_for_month",
        "commencements_for_month": "data_commencements_for_month",
    }

    numeric_cols = [
        "year", "month",
        "data_ytd_enrolments", "data_ytd_commencements",
        "data_as_at_1st_month", "data_enrolments_for_month", "data_commencements_for_month",
    ]

    frames = []
    for sheet in xl.sheet_names:
        # Skip obvious non-data sheets
        if any(x in sheet.lower() for x in ["note", "content", "glossary", "source", "readme"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Year", "Month", "Nationality", "Sector", "State"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            if "year" not in df.columns:
                log.debug(f"  Sheet '{sheet}' — no 'year' column, skipping")
                continue

            df = df.rename(columns=rename)

            if "month" in df.columns:
                # Month is an abbreviated name ("Jul", "Jan"), not a number.
                df["month"] = df["month"].map(_month_name_to_num)

            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=[c for c in ["year", "month"] if c in df.columns])
            if df.empty:
                continue

            log.info(f"  Sheet '{sheet}': {len(df):,} rows")
            frames.append(df)
        except Exception as e:
            log.warning(f"  Sheet '{sheet}': {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in [
        "year", "month", "region", "nationality", "state", "provider_type", "sector",
        "broad_field_of_education", "narrow_field_of_education", "detailed_field_of_education",
        "level_of_study", "foundation", "new_to_australia", "ends_this_year",
        "data_ytd_enrolments", "data_ytd_commencements",
        "data_as_at_1st_month", "data_enrolments_for_month", "data_commencements_for_month",
        # 'total' deliberately excluded -- proven exact duplicate of data_ytd_enrolments.
    ] if c in result.columns]
    return result[keep]


def parse_historical(path: Path) -> pd.DataFrame:
    """
    'International students studying in Australia (2005-2025).xlsx'
    Expected: annual data by nationality/state/sector.
    May be wide-format with years as columns.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  Historical sheets: {xl.sheet_names[:5]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "glossary", "source"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["Nationality", "Sector", "State", "Year", "Country"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = [c for c in df.columns if any(x in c for x in
                ["nationality", "country", "state", "sector", "measure", "type"])]
            year_cols = [c for c in df.columns if re.match(r"^\d{4}$", str(c).strip())]

            if year_cols and id_cols:
                long = df.melt(id_vars=id_cols, value_vars=year_cols,
                               var_name="year", value_name="value")
                long["year"] = pd.to_numeric(long["year"], errors="coerce")
                long["value"] = pd.to_numeric(long["value"], errors="coerce")
                long = long.dropna(subset=["value", "year"])
                long["measure"] = sheet.strip()

                # Standardise names
                col_map = {}
                for c in long.columns:
                    if "nationality" in c or "country" in c:
                        col_map[c] = "nationality"
                    elif "state" in c or "territory" in c:
                        col_map[c] = "state"
                    elif "sector" in c:
                        col_map[c] = "sector"
                long = long.rename(columns=col_map)
                frames.append(long)
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["year", "nationality", "state", "sector", "measure", "value"]
            if c in result.columns]
    return result[keep]


def parse_sa4(path: Path) -> pd.DataFrame:
    """
    SA4 enrolments by SA4 location, remoteness, sector, broad field.
    """
    xl = pd.ExcelFile(path)
    log.info(f"  SA4 sheets: {xl.sheet_names[:5]}")
    frames = []

    for sheet in xl.sheet_names:
        if any(x in sheet.lower() for x in ["note", "content", "source"]):
            continue
        try:
            raw = pd.read_excel(path, sheet_name=sheet, header=None)
            header_idx = _find_header(raw, ["SA4", "Remoteness", "Sector", "Field", "Year"])
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = [norm_col(c) for c in raw.iloc[header_idx].tolist()]
            df = df.dropna(how="all").reset_index(drop=True)

            id_cols = [c for c in df.columns if any(x in c for x in
                ["sa4", "remoteness", "sector", "field", "year", "month"])]
            val_cols = [c for c in df.columns if c not in id_cols and
                        df[c].dtype in ["float64", "int64"]]

            if val_cols and id_cols:
                long = df.melt(id_vars=id_cols, value_vars=val_cols,
                               var_name="measure", value_name="value")
                long["value"] = pd.to_numeric(long["value"], errors="coerce")
                long = long.dropna(subset=["value"])

                col_map = {}
                for c in long.columns:
                    if "sa4" in c:
                        col_map[c] = "sa4_name"
                    elif "remoteness" in c:
                        col_map[c] = "remoteness"
                    elif "sector" in c:
                        col_map[c] = "sector"
                    elif "field" in c:
                        col_map[c] = "broad_field"
                    elif "year" in c:
                        col_map[c] = "year"
                    elif "month" in c:
                        col_map[c] = "month"
                long = long.rename(columns=col_map)
                frames.append(long)
        except Exception as e:
            log.warning(f"  Sheet {sheet}: {e}")

    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["year", "month", "sa4_name", "remoteness",
                         "sector", "broad_field", "measure", "value"]
            if c in result.columns]
    return result[keep]


PARSERS = {
    "parse_pivot_basic":    parse_pivot_basic,
    "parse_pivot_detailed": parse_pivot_detailed,
    "parse_historical":     parse_historical,
    "parse_sa4":            parse_sa4,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(force: bool = False, local_only: bool = False, dry_run: bool = False,
        db_path: Path = DB_PATH):
    conn = get_db(db_path, SCHEMA)
    total = 0

    for src in SOURCES:
        log.info(f"\n{'─'*55}")
        log.info(f"[Education] {src['desc']}")

        # Find file
        path = None
        if not local_only and src.get("url"):
            try:
                path = download_file(src["url"], RAW_DIR, src["filename"], force=force)
            except Exception as e:
                log.warning(f"  Download failed: {e}")

        if path is None or not path.exists():
            # Try local glob
            if src.get("local_glob"):
                matches = sorted(RAW_DIR.glob(src["local_glob"]))
                if matches:
                    path = matches[-1]
                    log.info(f"  Local: {path.name}")
            elif src["parser"] in LOCAL_FILES:
                path = LOCAL_FILES[src["parser"]]

        if path is None or not path.exists():
            log.warning(f"  ⚠️  No file found — skipping")
            continue

        # Parse
        parser = PARSERS.get(src["parser"])
        if not parser:
            log.error(f"  No parser: {src['parser']}")
            continue

        try:
            df = parser(path)
        except Exception as e:
            log.error(f"  ❌ Parse failed: {e}")
            continue

        if df.empty:
            log.warning(f"  ⚠️  Empty result")
            continue

        log.info(f"  Parsed {len(df):,} rows")
        df = add_etl_meta(df, f"education/{path.name}")
        n = upsert_df(df, src["table"], conn, dry_run=dry_run)
        total += n

    log.info(f"\n✅ Education ETL complete — {total:,} rows total")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC ETL: Department of Education")
    ap.add_argument("--force",      action="store_true")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--db",         default=str(DB_PATH))
    args = ap.parse_args()
    run(force=args.force, local_only=args.local_only, dry_run=args.dry_run, db_path=Path(args.db))
