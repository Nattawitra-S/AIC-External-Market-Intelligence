"""
tests/test_education_release_manifest.py
==========================================
Tests for ETL/education_release_manifest.py (schema, hashing, read/write,
validation) and ETL/etl_education_v2.py's verify_local_only_release_manifest()
(the --local-only integration point).

All tests use temporary files only -- no network access, no real workbooks,
no manifest for the actual current local files is ever created.

Run:
    pytest tests/test_education_release_manifest.py -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from ETL import etl_education_v2 as etl_edu
from ETL.education_release_manifest import (
    ManifestValidationError,
    build_manifest,
    compute_sha256,
    month_label,
    read_manifest,
    validate_local_files_against_manifest,
    write_manifest,
)


class TestMonthLabelAndHashing(unittest.TestCase):

    def test_month_label(self):
        self.assertEqual(month_label(2026, 5), "May 2026")
        self.assertEqual(month_label(2025, 12), "December 2025")

    def test_compute_sha256_is_deterministic(self):
        tmp = Path(tempfile.mkdtemp(prefix="edu_hash_test_"))
        try:
            p = tmp / "f.bin"
            p.write_bytes(b"hello world" * 1000)
            h1 = compute_sha256(p)
            h2 = compute_sha256(p)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # hex sha256
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_compute_sha256_changes_with_content(self):
        tmp = Path(tempfile.mkdtemp(prefix="edu_hash_test_"))
        try:
            p = tmp / "f.bin"
            p.write_bytes(b"content A")
            h1 = compute_sha256(p)
            p.write_bytes(b"content B")
            h2 = compute_sha256(p)
            self.assertNotEqual(h1, h2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBuildWriteReadManifest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_manifest_test_"))
        self.basic_path = self.tmp / "basic_downloaded.xlsx"
        self.detailed_path = self.tmp / "detailed_downloaded.xlsx"
        self.basic_path.write_bytes(b"BASIC-CONTENT-XYZ")
        self.detailed_path.write_bytes(b"DETAILED-CONTENT-ABC-LONGER")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_manifest_records_correct_hashes_and_sizes(self):
        manifest = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )
        self.assertEqual(manifest.release_label, "May 2026")
        self.assertEqual(manifest.basic.size_bytes, self.basic_path.stat().st_size)
        self.assertEqual(manifest.basic.sha256, compute_sha256(self.basic_path))
        self.assertEqual(manifest.detailed.size_bytes, self.detailed_path.stat().st_size)
        self.assertEqual(manifest.detailed.sha256, compute_sha256(self.detailed_path))
        self.assertEqual(manifest.basic.release_year, 2026)
        self.assertEqual(manifest.detailed.release_month, 5)

    def test_write_then_read_roundtrips(self):
        manifest = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )
        dest = self.tmp / "manifest.json"
        write_manifest(manifest, dest)

        loaded = read_manifest(dest)
        self.assertEqual(loaded, manifest)

    def test_manifest_json_has_no_absolute_paths_or_secrets(self):
        manifest = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )
        raw_json = json.dumps(manifest.to_dict())
        self.assertNotIn(str(self.tmp), raw_json)
        self.assertNotIn("password", raw_json.lower())
        self.assertNotIn("MYSQL_", raw_json)

    def test_read_manifest_returns_none_when_missing(self):
        self.assertIsNone(read_manifest(self.tmp / "does_not_exist.json"))


class TestValidateLocalFilesAgainstManifest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_manifest_validate_test_"))
        self.basic_path = self.tmp / "Pivot_Basic_All_web.xlsx"
        self.detailed_path = self.tmp / "Pivot_Detailed_Latest_web.xlsx"
        self.basic_path.write_bytes(b"BASIC-CONTENT")
        self.detailed_path.write_bytes(b"DETAILED-CONTENT-LONGER")
        self.manifest = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_matching_files_pass(self):
        validate_local_files_against_manifest(self.manifest, self.basic_path, self.detailed_path)  # no raise

    def test_wrong_filename_fails(self):
        wrong_name = self.tmp / "Wrong_Name.xlsx"
        wrong_name.write_bytes(b"BASIC-CONTENT")
        with self.assertRaises(ManifestValidationError):
            validate_local_files_against_manifest(self.manifest, wrong_name, self.detailed_path)

    def test_missing_workbook_fails(self):
        missing = self.tmp / "Pivot_Basic_All_web.xlsx.does_not_exist"
        with self.assertRaises(ManifestValidationError) as ctx:
            validate_local_files_against_manifest(self.manifest, missing, self.detailed_path)
        # filename mismatch will trigger first since missing.name != manifest filename;
        # use a same-named-but-absent path instead for a true "missing" case:
        truly_missing_basic = self.tmp / "Pivot_Basic_All_web.xlsx"
        truly_missing_basic.unlink()
        with self.assertRaises(ManifestValidationError) as ctx2:
            validate_local_files_against_manifest(self.manifest, truly_missing_basic, self.detailed_path)
        self.assertIn("missing", str(ctx2.exception).lower())

    def test_modified_file_hash_mismatch_fails(self):
        self.basic_path.write_bytes(b"TAMPERED-CONTENT-DIFFERENT-LENGTH-TOO")
        with self.assertRaises(ManifestValidationError) as ctx:
            validate_local_files_against_manifest(self.manifest, self.basic_path, self.detailed_path)
        self.assertIn("mismatch", str(ctx.exception).lower())

    def test_size_only_mismatch_fails(self):
        """Same length-preserving tamper still changes the hash and must
        still be caught even if we only check size first."""
        # Overwrite with same-length but different content.
        original_len = len(self.basic_path.read_bytes())
        self.basic_path.write_bytes(b"X" * original_len)
        with self.assertRaises(ManifestValidationError):
            validate_local_files_against_manifest(self.manifest, self.basic_path, self.detailed_path)

    def test_basic_detailed_release_mismatch_fails(self):
        """A manifest that's been hand-edited/corrupted so Basic and
        Detailed disagree about which release they belong to must be
        rejected, not silently trusted."""
        from ETL.education_release_manifest import ReleaseManifest, WorkbookRecord

        tampered = ReleaseManifest(
            release_label=self.manifest.release_label,
            release_year=self.manifest.release_year,
            release_month=self.manifest.release_month,
            publication_page_url=self.manifest.publication_page_url,
            downloaded_at_utc=self.manifest.downloaded_at_utc,
            basic=self.manifest.basic,
            detailed=WorkbookRecord(
                filename=self.manifest.detailed.filename,
                url=self.manifest.detailed.url,
                size_bytes=self.manifest.detailed.size_bytes,
                sha256=self.manifest.detailed.sha256,
                release_year=2025,  # deliberately different from Basic's 2026
                release_month=12,
            ),
        )
        with self.assertRaises(ManifestValidationError) as ctx:
            validate_local_files_against_manifest(tampered, self.basic_path, self.detailed_path)
        self.assertIn("mismatch", str(ctx.exception).lower())


class TestVerifyLocalOnlyReleaseManifest(unittest.TestCase):
    """Tests for etl_education_v2.verify_local_only_release_manifest()."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_local_only_manifest_test_"))
        self.basic_path = self.tmp / "Pivot_Basic_All_web.xlsx"
        self.detailed_path = self.tmp / "Pivot_Detailed_Latest_web.xlsx"
        self.basic_path.write_bytes(b"BASIC-CONTENT")
        self.detailed_path.write_bytes(b"DETAILED-CONTENT-LONGER")
        self.manifest_path = self.tmp / "education_release_manifest.json"
        self.basic_raw_split = self.tmp / "Pivot_Basic_All_web_extracted_raw_split.xlsx"
        self.detailed_raw_split = self.tmp / "Pivot_Detailed_Latest_web_extracted_raw_split.xlsx"
        self.basic_raw_split.write_bytes(b"BASIC-RAW-SPLIT-CONTENT")
        self.detailed_raw_split.write_bytes(b"DETAILED-RAW-SPLIT-CONTENT-LONGER")

        self._patches = [
            patch.object(etl_edu, "EXTRACTOR_INPUT_DIR", self.tmp),
            patch.object(etl_edu, "PIVOT_BASIC_INPUT", self.basic_path),
            patch.object(etl_edu, "PIVOT_DETAILED_INPUT", self.detailed_path),
            patch.object(etl_edu, "PIVOT_BASIC_RAW_SPLIT", self.basic_raw_split),
            patch.object(etl_edu, "PIVOT_DETAILED_RAW_SPLIT", self.detailed_raw_split),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_downloaded_manifest(self):
        return build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )

    def _write_downloaded_manifest(self):
        """status='downloaded' only -- as if extraction never ran/completed."""
        write_manifest(self._build_downloaded_manifest(), self.manifest_path)

    def _write_extracted_manifest(self):
        """status='extracted' with raw-split hashes recorded, matching the
        current raw-split files on disk -- the fully-verified case."""
        from ETL.education_release_manifest import mark_extracted
        downloaded = self._build_downloaded_manifest()
        extracted = mark_extracted(downloaded, self.basic_raw_split, self.detailed_raw_split)
        write_manifest(extracted, self.manifest_path)

    def test_extracted_manifest_passes_in_dry_run(self):
        self._write_extracted_manifest()
        etl_edu.verify_local_only_release_manifest(dry_run=True)  # must not raise

    def test_extracted_manifest_passes_in_real_run(self):
        self._write_extracted_manifest()
        etl_edu.verify_local_only_release_manifest(dry_run=False)  # must not raise

    def test_mismatched_input_manifest_fails_in_both_modes(self):
        self._write_extracted_manifest()
        self.basic_path.write_bytes(b"TAMPERED")  # invalidates the recorded input hash
        with self.assertRaises(RuntimeError):
            etl_edu.verify_local_only_release_manifest(dry_run=True)
        with self.assertRaises(RuntimeError):
            etl_edu.verify_local_only_release_manifest(dry_run=False)

    def test_missing_manifest_warns_in_dry_run_but_does_not_raise(self):
        # No manifest written -- legacy local files.
        with patch.object(etl_edu.log, "warning") as mock_warning:
            etl_edu.verify_local_only_release_manifest(dry_run=True)  # must not raise
        warned_text = " ".join(str(c) for c in mock_warning.call_args_list)
        self.assertIn("UNVERIFIED LEGACY", warned_text)

    def test_missing_manifest_fails_in_real_run(self):
        # No manifest written, and this is a real (non-dry-run) --local-only
        # run -- must fail, no silent override.
        with self.assertRaises(RuntimeError) as ctx:
            etl_edu.verify_local_only_release_manifest(dry_run=False)
        self.assertIn("no override is currently implemented", str(ctx.exception).lower())

    # ── The provenance-gap fix: status='downloaded' (extraction incomplete) ──

    def test_status_downloaded_fails_in_dry_run(self):
        """The exact failure scenario this fix targets: inputs downloaded
        and manifest-verified, but extraction never completed for them
        (manifest still says 'downloaded') -- must fail even in --dry-run,
        since dry-run still goes on to parse the raw-split files."""
        self._write_downloaded_manifest()
        with self.assertRaises(RuntimeError) as ctx:
            etl_edu.verify_local_only_release_manifest(dry_run=True)
        self.assertIn("not 'extracted'", str(ctx.exception))

    def test_status_downloaded_fails_in_real_run(self):
        self._write_downloaded_manifest()
        with self.assertRaises(RuntimeError) as ctx:
            etl_edu.verify_local_only_release_manifest(dry_run=False)
        self.assertIn("not 'extracted'", str(ctx.exception))

    def test_stale_raw_split_after_extracted_status_fails(self):
        """status='extracted' but the raw-split file on disk has since
        changed (e.g. a later, unrelated extraction overwrote it without
        updating the manifest) -- must be caught by the raw-split hash
        check, not just the status flag."""
        self._write_extracted_manifest()
        self.basic_raw_split.write_bytes(b"DIFFERENT-CONTENT-NOW")
        with self.assertRaises(RuntimeError) as ctx:
            etl_edu.verify_local_only_release_manifest(dry_run=True)
        self.assertIn("raw-split", str(ctx.exception).lower())

    def test_missing_raw_split_after_extracted_status_fails(self):
        self._write_extracted_manifest()
        self.detailed_raw_split.unlink()
        with self.assertRaises(RuntimeError):
            etl_edu.verify_local_only_release_manifest(dry_run=True)


class TestMarkExtracted(unittest.TestCase):
    """Direct tests of education_release_manifest.mark_extracted()."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_mark_extracted_test_"))
        self.basic_path = self.tmp / "Pivot_Basic_All_web.xlsx"
        self.detailed_path = self.tmp / "Pivot_Detailed_Latest_web.xlsx"
        self.basic_path.write_bytes(b"BASIC")
        self.detailed_path.write_bytes(b"DETAILED-LONGER")
        self.basic_raw_split = self.tmp / "basic_raw_split.xlsx"
        self.detailed_raw_split = self.tmp / "detailed_raw_split.xlsx"
        self.basic_raw_split.write_bytes(b"BASIC-RAW-SPLIT")
        self.detailed_raw_split.write_bytes(b"DETAILED-RAW-SPLIT-LONGER")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_extracted_sets_status_and_raw_split_fields(self):
        from ETL.education_release_manifest import STATUS_DOWNLOADED, STATUS_EXTRACTED, mark_extracted

        downloaded = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )
        self.assertEqual(downloaded.status, STATUS_DOWNLOADED)
        self.assertIsNone(downloaded.basic.raw_split_filename)

        extracted = mark_extracted(downloaded, self.basic_raw_split, self.detailed_raw_split)
        self.assertEqual(extracted.status, STATUS_EXTRACTED)
        self.assertEqual(extracted.basic.raw_split_filename, self.basic_raw_split.name)
        self.assertEqual(extracted.basic.raw_split_size_bytes, self.basic_raw_split.stat().st_size)
        self.assertEqual(extracted.basic.raw_split_sha256, compute_sha256(self.basic_raw_split))
        self.assertEqual(extracted.detailed.raw_split_filename, self.detailed_raw_split.name)

        # Original download-time fields (hash/size/url of the INPUT) must be preserved.
        self.assertEqual(extracted.basic.sha256, downloaded.basic.sha256)
        self.assertEqual(extracted.basic.url, downloaded.basic.url)

    def test_mark_extracted_does_not_mutate_input_manifest(self):
        from ETL.education_release_manifest import mark_extracted

        downloaded = build_manifest(
            release_year=2026, release_month=5,
            publication_page_url="https://www.education.gov.au/x",
            basic_filename="Pivot_Basic_All_web.xlsx",
            basic_url="https://www.education.gov.au/download/basic/xlsx",
            basic_path=self.basic_path,
            detailed_filename="Pivot_Detailed_Latest_web.xlsx",
            detailed_url="https://www.education.gov.au/download/detailed/xlsx",
            detailed_path=self.detailed_path,
        )
        mark_extracted(downloaded, self.basic_raw_split, self.detailed_raw_split)
        self.assertEqual(downloaded.status, "downloaded")  # unchanged (frozen dataclass)


if __name__ == "__main__":
    unittest.main()
