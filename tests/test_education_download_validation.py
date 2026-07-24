"""
tests/test_education_download_validation.py
=============================================
Tests for the XLSX/pivot-cache validation and atomic two-file (Basic +
Detailed) replacement logic in ETL/etl_education_v2.py.

No live network access -- download_file() is mocked throughout.

Run:
    pytest tests/test_education_download_validation.py -v
"""
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from ETL import etl_education_v2 as etl_edu


def _write_tiny_pivot_cache_zip(path: Path, padded_to: int = 11_000) -> None:
    """A minimal but structurally valid pivot-cache .xlsx: a ZIP archive
    containing pivotCacheDefinition/pivotCacheRecords parts, padded past
    the 10KB 'implausibly small' floor with an inert extra ZIP entry."""
    defn_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'refreshedBy="test" refreshedDate="45000" createdVersion="1" refreshedVersion="1" recordCount="1">'
        '<cacheSource type="worksheet"><worksheetSource ref="A1:B2" sheet="Sheet1"/></cacheSource>'
        '<cacheFields count="1"><cacheField name="Total" numFmtId="0">'
        '<sharedItems containsSemiMixedTypes="0" containsString="0" containsNumber="1" containsInteger="1" '
        'minValue="1" maxValue="1"><n v="1"/></sharedItems></cacheField></cacheFields>'
        '</pivotCacheDefinition>'
    )
    records_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheRecords xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1">'
        '<r><n v="1"/></r></pivotCacheRecords>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("xl/pivotCache/pivotCacheDefinition1.xml", defn_xml)
        z.writestr("xl/pivotCache/pivotCacheRecords1.xml", records_xml)
        # Pad past the 10KB floor with a harmless filler entry.
        filler_needed = max(0, padded_to - path.stat().st_size if path.exists() else padded_to)
        z.writestr("padding.txt", "x" * padded_to)


class TestValidatePivotCacheXlsx(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_validate_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accepts_real_pivot_cache_structure(self):
        p = self.tmp / "good.xlsx"
        _write_tiny_pivot_cache_zip(p)
        etl_edu._validate_pivot_cache_xlsx(p)  # must not raise

    def test_rejects_missing_file(self):
        with self.assertRaises(ValueError):
            etl_edu._validate_pivot_cache_xlsx(self.tmp / "does_not_exist.xlsx")

    def test_rejects_empty_file(self):
        p = self.tmp / "empty.xlsx"
        p.write_bytes(b"")
        with self.assertRaises(ValueError):
            etl_edu._validate_pivot_cache_xlsx(p)

    def test_rejects_html_error_page_saved_as_xlsx(self):
        """The core failure mode this guards against: a government
        error/redirect page saved with an .xlsx extension."""
        p = self.tmp / "error_page.xlsx"
        p.write_text("<html><head><title>404 Not Found</title></head>"
                      "<body>Page not found</body></html>" * 200)  # padded, still not a ZIP
        with self.assertRaises(ValueError) as ctx:
            etl_edu._validate_pivot_cache_xlsx(p)
        self.assertIn("ZIP/XLSX signature", str(ctx.exception))

    def test_rejects_implausibly_small_file(self):
        p = self.tmp / "tiny.xlsx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("hello.txt", "hi")  # valid zip, but way under 10KB
        with self.assertRaises(ValueError) as ctx:
            etl_edu._validate_pivot_cache_xlsx(p)
        self.assertIn("implausibly small", str(ctx.exception))

    def test_rejects_valid_zip_without_pivot_cache_parts(self):
        p = self.tmp / "wrong_structure.xlsx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            z.writestr("padding.txt", "x" * 11_000)
        with self.assertRaises(ValueError) as ctx:
            etl_edu._validate_pivot_cache_xlsx(p)
        self.assertIn("pivot cache definition/records", str(ctx.exception))


class TestDownloadAndValidateReleasePair(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_pair_test_"))
        self.basic_input = self.tmp / "Pivot_Basic_All_web.xlsx"
        self.detailed_input = self.tmp / "Pivot_Detailed_Latest_web.xlsx"
        self.manifest_path = self.tmp / "education_release_manifest.json"
        # Seed "existing" canonical inputs to prove they're preserved on failure.
        self.basic_input.write_bytes(b"OLD-BASIC-CONTENT")
        self.detailed_input.write_bytes(b"OLD-DETAILED-CONTENT")

        self._patches = [
            patch.object(etl_edu, "EXTRACTOR_INPUT_DIR", self.tmp),
            patch.object(etl_edu, "PIVOT_BASIC_INPUT", self.basic_input),
            patch.object(etl_edu, "PIVOT_DETAILED_INPUT", self.detailed_input),
        ]
        for p in self._patches:
            p.start()

        from ETL.education_discovery import DiscoveredRelease
        self.release = DiscoveredRelease(
            year=2026, month=5,
            basic_url="https://www.education.gov.au/download/x/basic/xlsx",
            detailed_url="https://www.education.gov.au/download/x/detailed/xlsx",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_succeed_atomically_replaces_both_and_writes_manifest(self):
        def fake_download_file(url, dest_dir, filename, force=False):
            dest = dest_dir / filename
            _write_tiny_pivot_cache_zip(dest)
            return dest

        with patch.object(etl_edu, "download_file", side_effect=fake_download_file):
            basic_path, detailed_path = etl_edu.download_and_validate_release_pair(self.release)

        self.assertEqual(basic_path, self.basic_input)
        self.assertEqual(detailed_path, self.detailed_input)
        self.assertNotEqual(self.basic_input.read_bytes(), b"OLD-BASIC-CONTENT")
        self.assertNotEqual(self.detailed_input.read_bytes(), b"OLD-DETAILED-CONTENT")

        # Manifest was written with correct content.
        self.assertTrue(self.manifest_path.exists())
        import json
        data = json.loads(self.manifest_path.read_text())
        self.assertEqual(data["release_label"], "May 2026")
        self.assertEqual(data["release_year"], 2026)
        self.assertEqual(data["release_month"], 5)
        self.assertEqual(data["basic"]["filename"], "Pivot_Basic_All_web.xlsx")
        self.assertEqual(data["detailed"]["filename"], "Pivot_Detailed_Latest_web.xlsx")
        self.assertEqual(data["basic"]["size_bytes"], self.basic_input.stat().st_size)
        self.assertEqual(data["detailed"]["size_bytes"], self.detailed_input.stat().st_size)
        # No local absolute paths or secrets leaked into the manifest.
        self.assertNotIn(str(self.tmp), json.dumps(data))

        # No leftover .part/.bak files.
        for suffix in (".part", ".bak"):
            self.assertFalse((self.tmp / (self.basic_input.name + suffix)).exists())
            self.assertFalse((self.tmp / (self.detailed_input.name + suffix)).exists())
            self.assertFalse((self.tmp / (self.manifest_path.name + suffix)).exists())

    def test_detailed_failure_preserves_both_old_files_and_no_manifest(self):
        """If Basic downloads fine but Detailed fails validation, NEITHER
        canonical input is replaced -- never leaves Basic updated while
        Detailed is stale, or vice versa -- and no manifest is written."""
        def fake_download_file(url, dest_dir, filename, force=False):
            dest = dest_dir / filename
            if "Detailed" in filename:
                dest.write_text("<html>404 not found</html>" * 500)  # invalid: not a real zip
            else:
                _write_tiny_pivot_cache_zip(dest)
            return dest

        with patch.object(etl_edu, "download_file", side_effect=fake_download_file):
            with self.assertRaises(ValueError):
                etl_edu.download_and_validate_release_pair(self.release)

        self.assertEqual(self.basic_input.read_bytes(), b"OLD-BASIC-CONTENT",
                          "Basic must remain the OLD file -- not partially updated")
        self.assertEqual(self.detailed_input.read_bytes(), b"OLD-DETAILED-CONTENT",
                          "Detailed must remain the OLD file")
        self.assertFalse(self.manifest_path.exists(), "no manifest should be written on failure")
        self.assertFalse((self.tmp / (self.basic_input.name + ".part")).exists(),
                          "leftover Basic .part must be cleaned up")
        self.assertFalse((self.tmp / (self.detailed_input.name + ".part")).exists(),
                          "leftover Detailed .part must be cleaned up")

    def test_basic_failure_preserves_both_old_files_and_never_downloads_detailed(self):
        """If Basic itself fails validation, Detailed is never even
        downloaded -- fail fast, preserve both old files, no manifest."""
        detailed_download_attempted = []

        def fake_download_file(url, dest_dir, filename, force=False):
            dest = dest_dir / filename
            if "Detailed" in filename:
                detailed_download_attempted.append(True)
                _write_tiny_pivot_cache_zip(dest)
            else:
                dest.write_text("<html>404 not found</html>" * 500)
            return dest

        with patch.object(etl_edu, "download_file", side_effect=fake_download_file):
            with self.assertRaises(ValueError):
                etl_edu.download_and_validate_release_pair(self.release)

        self.assertEqual(detailed_download_attempted, [], "Detailed must never be downloaded if Basic already failed")
        self.assertEqual(self.basic_input.read_bytes(), b"OLD-BASIC-CONTENT")
        self.assertEqual(self.detailed_input.read_bytes(), b"OLD-DETAILED-CONTENT")
        self.assertFalse(self.manifest_path.exists())

    def test_replacement_failure_restores_previous_files_and_manifest(self):
        """If the FINAL atomic-replace step itself fails (both downloads
        and validation already succeeded), the previous Basic, Detailed,
        AND manifest must all be restored -- proving the rollback covers
        the replace phase, not just the download/validation phase."""
        # Seed an existing "previous" manifest to prove it gets restored.
        self.manifest_path.write_text('{"release_label": "December 2025", "old": true}')

        def fake_download_file(url, dest_dir, filename, force=False):
            dest = dest_dir / filename
            _write_tiny_pivot_cache_zip(dest)
            return dest

        with patch.object(etl_edu, "download_file", side_effect=fake_download_file), \
             patch.object(etl_edu, "_atomic_replace_release_files",
                          side_effect=RuntimeError("simulated disk failure during replace")):
            with self.assertRaises(RuntimeError):
                etl_edu.download_and_validate_release_pair(self.release)

        # _atomic_replace_release_files is itself mocked to fail before
        # doing anything, so the live files must be untouched, and the
        # downloaded .part files must be cleaned up by the outer except.
        self.assertEqual(self.basic_input.read_bytes(), b"OLD-BASIC-CONTENT")
        self.assertEqual(self.detailed_input.read_bytes(), b"OLD-DETAILED-CONTENT")
        self.assertIn("December 2025", self.manifest_path.read_text())
        self.assertFalse((self.tmp / (self.basic_input.name + ".part")).exists())
        self.assertFalse((self.tmp / (self.detailed_input.name + ".part")).exists())
        self.assertFalse((self.tmp / (self.manifest_path.name + ".part")).exists())


class TestAtomicReplaceReleaseFiles(unittest.TestCase):
    """Direct tests of the 3-file rollback-protected replace primitive,
    independent of download/validation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_atomic_replace_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_succeed_replaces_all_and_cleans_backups(self):
        live_a = self.tmp / "a.txt"
        live_b = self.tmp / "b.txt"
        live_a.write_text("old-a")
        live_b.write_text("old-b")
        new_a = self.tmp / "a.txt.part"
        new_b = self.tmp / "b.txt.part"
        new_a.write_text("new-a")
        new_b.write_text("new-b")

        etl_edu._atomic_replace_release_files([(new_a, live_a), (new_b, live_b)])

        self.assertEqual(live_a.read_text(), "new-a")
        self.assertEqual(live_b.read_text(), "new-b")
        self.assertFalse((self.tmp / "a.txt.bak").exists())
        self.assertFalse((self.tmp / "b.txt.bak").exists())

    def test_first_time_no_existing_live_files(self):
        """No prior live files (first-ever run) -- must not error looking
        for something to back up, and must still succeed."""
        live_a = self.tmp / "a.txt"
        live_b = self.tmp / "b.txt"
        new_a = self.tmp / "a.txt.part"
        new_b = self.tmp / "b.txt.part"
        new_a.write_text("new-a")
        new_b.write_text("new-b")

        etl_edu._atomic_replace_release_files([(new_a, live_a), (new_b, live_b)])

        self.assertEqual(live_a.read_text(), "new-a")
        self.assertEqual(live_b.read_text(), "new-b")

    def test_second_replace_failing_restores_first(self):
        """If the second file's replace fails, the first file (already
        replaced) must be rolled back too -- proving partial-completion
        is not left half-applied."""
        live_a = self.tmp / "a.txt"
        live_b = self.tmp / "b.txt"
        live_a.write_text("old-a")
        live_b.write_text("old-b")
        new_a = self.tmp / "a.txt.part"
        new_a.write_text("new-a")
        # new_b deliberately does not exist -> its .replace() will raise.
        missing_new_b = self.tmp / "does_not_exist.part"

        with self.assertRaises(OSError):
            etl_edu._atomic_replace_release_files([(new_a, live_a), (missing_new_b, live_b)])

        self.assertEqual(live_a.read_text(), "old-a", "first file must be rolled back")
        self.assertEqual(live_b.read_text(), "old-b", "second file must remain untouched")

    def test_first_ever_run_second_replace_fails_removes_first_new_file(self):
        """First-ever run: no prior Basic, Detailed, or manifest live files
        at all. The first staged file's move succeeds, but a later one
        fails. The already-installed first file has no .bak to roll back
        to (nothing existed before it), so it must be actively removed --
        not left behind as an orphaned partial install."""
        live_basic = self.tmp / "basic.txt"
        live_detailed = self.tmp / "detailed.txt"
        live_manifest = self.tmp / "manifest.json"
        new_basic = self.tmp / "basic.txt.part"
        new_basic.write_text("new-basic")
        # Detailed and manifest .part files deliberately do not exist, so
        # their replace() calls raise before either is installed.
        missing_new_detailed = self.tmp / "does_not_exist_detailed.part"
        missing_new_manifest = self.tmp / "does_not_exist_manifest.part"

        with self.assertRaises(OSError):
            etl_edu._atomic_replace_release_files([
                (new_basic, live_basic),
                (missing_new_detailed, live_detailed),
                (missing_new_manifest, live_manifest),
            ])

        self.assertFalse(live_basic.exists(), "first-ever new file must be removed on rollback, not left behind")
        self.assertFalse(live_detailed.exists())
        self.assertFalse(live_manifest.exists())
        leftover = list(self.tmp.iterdir())
        self.assertEqual(leftover, [], f"no .bak/.part remnants should remain, found: {leftover}")


class TestDownloadAndExtractReleasePairManifestLifecycle(unittest.TestCase):
    """
    Integration-level tests for the provenance-gap fix: proves the full
    download_and_extract_release_pair() orchestration marks the manifest
    'extracted' only on a genuinely successful extraction, and leaves it
    at 'downloaded' (never silently promoted) if extraction fails -- this
    is the exact scenario the fix targets: new inputs download fine,
    extraction fails, and a later --local-only run must not trust the
    old/stale raw-split outputs still sitting on disk.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="edu_lifecycle_test_"))
        self.basic_input = self.tmp / "Pivot_Basic_All_web.xlsx"
        self.detailed_input = self.tmp / "Pivot_Detailed_Latest_web.xlsx"
        self.basic_raw_split = self.tmp / "Pivot_Basic_All_web_extracted_raw_split.xlsx"
        self.detailed_raw_split = self.tmp / "Pivot_Detailed_Latest_web_extracted_raw_split.xlsx"
        self.manifest_path = self.tmp / "education_release_manifest.json"

        self._patches = [
            patch.object(etl_edu, "EXTRACTOR_INPUT_DIR", self.tmp),
            patch.object(etl_edu, "PIVOT_BASIC_INPUT", self.basic_input),
            patch.object(etl_edu, "PIVOT_DETAILED_INPUT", self.detailed_input),
            patch.object(etl_edu, "PIVOT_BASIC_RAW_SPLIT", self.basic_raw_split),
            patch.object(etl_edu, "PIVOT_DETAILED_RAW_SPLIT", self.detailed_raw_split),
        ]
        for p in self._patches:
            p.start()

        from ETL.education_discovery import DiscoveredRelease
        self.release = DiscoveredRelease(
            year=2026, month=5,
            basic_url="https://www.education.gov.au/download/x/basic/xlsx",
            detailed_url="https://www.education.gov.au/download/x/detailed/xlsx",
        )

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_download_file(self, url, dest_dir, filename, force=False):
        dest = dest_dir / filename
        _write_tiny_pivot_cache_zip(dest)
        return dest

    def test_successful_extraction_marks_manifest_extracted(self):
        def fake_run_extractor_pair():
            # Simulate the extractor producing both raw-split outputs.
            self.basic_raw_split.write_bytes(b"BASIC-RAW-SPLIT-OUTPUT")
            self.detailed_raw_split.write_bytes(b"DETAILED-RAW-SPLIT-OUTPUT-LONGER")
            return self.basic_raw_split, self.detailed_raw_split

        with patch.object(etl_edu, "download_file", side_effect=self._fake_download_file), \
             patch("ETL.education_discovery.discover_current_education_urls", return_value=self.release), \
             patch.object(etl_edu, "_run_extractor_pair", side_effect=fake_run_extractor_pair):
            basic_out, detailed_out = etl_edu.download_and_extract_release_pair()

        self.assertEqual(basic_out, self.basic_raw_split)
        self.assertEqual(detailed_out, self.detailed_raw_split)

        from ETL.education_release_manifest import STATUS_EXTRACTED, read_manifest
        manifest = read_manifest(self.manifest_path)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.status, STATUS_EXTRACTED)
        self.assertEqual(manifest.basic.raw_split_filename, self.basic_raw_split.name)
        self.assertEqual(manifest.detailed.raw_split_filename, self.detailed_raw_split.name)

    def test_extraction_failure_leaves_manifest_at_downloaded(self):
        """The exact bug scenario: inputs download and validate fine
        (manifest written as 'downloaded'), but extraction fails --
        the manifest must NOT be promoted to 'extracted'."""
        with patch.object(etl_edu, "download_file", side_effect=self._fake_download_file), \
             patch("ETL.education_discovery.discover_current_education_urls", return_value=self.release), \
             patch.object(etl_edu, "_run_extractor_pair",
                          side_effect=RuntimeError("simulated extractor failure")):
            with self.assertRaises(RuntimeError):
                etl_edu.download_and_extract_release_pair()

        from ETL.education_release_manifest import STATUS_DOWNLOADED, read_manifest
        manifest = read_manifest(self.manifest_path)
        self.assertIsNotNone(manifest, "the input download itself succeeded, so a manifest should exist")
        self.assertEqual(manifest.status, STATUS_DOWNLOADED,
                          "manifest must NOT be marked 'extracted' when extraction failed")

        # Simulating the user's exact failure scenario: a later --local-only
        # run must now refuse to proceed, because status stayed 'downloaded'.
        # (Old raw-split outputs, if any existed from a prior release, are
        # exactly what this must protect against trusting.)
        with self.assertRaises(RuntimeError) as ctx:
            etl_edu.verify_local_only_release_manifest(dry_run=True)
        self.assertIn("not 'extracted'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
