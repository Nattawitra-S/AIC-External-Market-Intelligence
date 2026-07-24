"""
tests/test_tracked_extractor_cli.py
====================================
Tests for the Phase 1 CLI upgrade to ETL/tools/extract_fixed.py: backward-
compatible optional-filename behaviour (auto-discovers Basic + Detailed
when omitted, preserves the exact single-filename contract when provided).

INPUT_DIR/OUTPUT_DIR are monkeypatched to an isolated temp directory for
every test -- the real raw_data/department_of_education/File_extractor 2/
input and output directories (which contain real production data) are
never read from or written to.

Run:
    pytest tests/test_tracked_extractor_cli.py -v
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

from ETL.tools import extract_fixed


def _write_tiny_pivot_cache_zip(path: Path) -> None:
    """Minimal but structurally valid pivot-cache .xlsx for a real (not
    mocked) end-to-end extraction sanity check."""
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


class _IsolatedDirsMixin:
    """Monkeypatches extract_fixed.INPUT_DIR / OUTPUT_DIR to a temp dir for
    the duration of each test -- the real production directories are never
    touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="extract_fixed_cli_test_"))
        self.input_dir = self.tmp / "input"
        self.output_dir = self.tmp / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        self._patches = [
            patch.object(extract_fixed, "INPUT_DIR", self.input_dir),
            patch.object(extract_fixed, "OUTPUT_DIR", self.output_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestExplicitFilenameModeUnchanged(_IsolatedDirsMixin, unittest.TestCase):
    """The single-filename CLI contract must be byte-for-byte unchanged."""

    def test_missing_explicit_file_exits_1_with_original_message(self):
        with patch.object(sys, "argv", ["extract_fixed.py", "does_not_exist.xlsx"]):
            with patch("builtins.print") as mock_print:
                with self.assertRaises(SystemExit) as ctx:
                    extract_fixed.main()
        self.assertEqual(ctx.exception.code, 1)
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Input file not found", printed)

    def test_absolute_path_rejected(self):
        with patch.object(sys, "argv", ["extract_fixed.py", "/etc/passwd"]):
            with patch("builtins.print"):
                with self.assertRaises(SystemExit) as ctx:
                    extract_fixed.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_explicit_filename_processes_only_that_file(self):
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Basic_All_web.xlsx")
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Detailed_Latest_web.xlsx")

        with patch.object(extract_fixed, "process_file") as mock_process:
            with patch.object(sys, "argv", ["extract_fixed.py", "Pivot_Basic_All_web.xlsx"]):
                extract_fixed.main()

        self.assertEqual(mock_process.call_count, 1)
        self.assertEqual(mock_process.call_args[0][0].name, "Pivot_Basic_All_web.xlsx")


class TestAutoModeBackwardCompatible(_IsolatedDirsMixin, unittest.TestCase):
    """New behaviour: omitted filename -> auto-discover Basic + Detailed."""

    def test_both_present_processes_both(self):
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Basic_All_web.xlsx")
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Detailed_Latest_web.xlsx")

        with patch.object(extract_fixed, "process_file") as mock_process:
            with patch.object(sys, "argv", ["extract_fixed.py"]):
                extract_fixed.main()

        self.assertEqual(mock_process.call_count, 2)
        processed_names = {c[0][0].name for c in mock_process.call_args_list}
        self.assertEqual(processed_names, {"Pivot_Basic_All_web.xlsx", "Pivot_Detailed_Latest_web.xlsx"})

    def test_one_missing_skips_with_warning_processes_the_other(self):
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Basic_All_web.xlsx")
        # Detailed intentionally absent.

        with patch.object(extract_fixed, "process_file") as mock_process:
            with patch.object(sys, "argv", ["extract_fixed.py"]):
                with patch("builtins.print") as mock_print:
                    extract_fixed.main()

        self.assertEqual(mock_process.call_count, 1)
        self.assertEqual(mock_process.call_args[0][0].name, "Pivot_Basic_All_web.xlsx")
        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Skipping file(s) not currently present", printed)
        self.assertIn("Pivot_Detailed_Latest_web.xlsx", printed)

    def test_neither_present_fails_without_processing_anything(self):
        with patch.object(extract_fixed, "process_file") as mock_process:
            with patch.object(sys, "argv", ["extract_fixed.py"]):
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as ctx:
                        extract_fixed.main()

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(mock_process.call_count, 0)

    def test_real_end_to_end_auto_extraction_tiny_fixtures(self):
        """One lightweight, non-mocked sanity check that the auto-mode
        path genuinely produces real output for tiny synthetic fixtures --
        not just that process_file gets called with the right arguments."""
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Basic_All_web.xlsx")
        _write_tiny_pivot_cache_zip(self.input_dir / "Pivot_Detailed_Latest_web.xlsx")

        with patch.object(sys, "argv", ["extract_fixed.py"]):
            extract_fixed.main()

        basic_out = self.output_dir / "Pivot_Basic_All_web_extracted.xlsx"
        detailed_out = self.output_dir / "Pivot_Detailed_Latest_web_extracted.xlsx"
        self.assertTrue(basic_out.exists() or (self.output_dir / "Pivot_Basic_All_web_extracted_raw_split.xlsx").exists())
        self.assertTrue(detailed_out.exists() or (self.output_dir / "Pivot_Detailed_Latest_web_extracted_raw_split.xlsx").exists())


class TestPathConstants(unittest.TestCase):
    """Confirm the Phase 1 upgrade fixed the stale DATA_DIR (found during
    review) without needing directory patching."""

    def test_data_dir_points_at_current_department_of_education_location(self):
        path_str = str(extract_fixed.DATA_DIR)
        self.assertIn("raw_data/department_of_education/File_extractor 2", path_str)
        self.assertNotIn("raw_data/File_extractor 2", path_str.replace(
            "raw_data/department_of_education/File_extractor 2", ""
        ))

    def test_auto_input_files_are_deterministic_not_glob(self):
        self.assertEqual(
            extract_fixed.AUTO_INPUT_FILES,
            ["Pivot_Basic_All_web.xlsx", "Pivot_Detailed_Latest_web.xlsx"],
        )


if __name__ == "__main__":
    unittest.main()
