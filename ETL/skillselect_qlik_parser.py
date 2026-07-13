"""
skillselect_qlik_parser.py
===========================
Phase 2: Parse Qlik Engine WebSocket JSON → pandas DataFrame
Australian Centre of English (AIC) - Market Intelligence Project

INPUT:  captures/ws_payload_*.json  (from AIC_SkillSelect_ETL.ipynb Cell 2)
OUTPUT: pandas DataFrame with columns:
        anzsco_code, occupation_name, visa_subclass, state,
        ceiling, invitations_issued, fill_rate_pct, trend, data_month

USAGE:
    from ETL.skillselect_qlik_parser import parse_ws_payload
    df = parse_ws_payload("captures/ws_payload_20260701.json")
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ── Qlik field name → AIC column mapping ───────────────────────────────────
# TODO: Update after running AIC_SkillSelect_ETL.ipynb Cell 3 and seeing actual column output
FIELD_MAP = {
    "ANZSCO Code":       "anzsco_code",
    "Occupation":        "occupation_name",
    "Visa Subclass":     "visa_subclass",
    "State/Territory":   "state",
    "Occupation Ceiling":"ceiling",
    "Invitations Issued":"invitations_issued",
    "Fill Rate":         "fill_rate_pct",
}

VISA_SUBCLASSES = {"189", "190", "491", "186", "482", "485"}


class QlikParser:
    """
    Parses Qlik Engine WebSocket message stream from SkillSelect.
    The Qlik Engine API (JSON-RPC 2.0) sends data via GetHyperCubeData
    responses containing 'qDataPages' → 'qMatrix' rows.
    Each cell: {qText: str, qNum: float, qState: str}
    """

    def __init__(self, payload_path: str | Path):
        self.payload_path = Path(payload_path)
        self.messages: list[dict] = []
        self.hypercube_responses: list[dict] = []
        self.raw_rows: list[dict] = []

    def load(self) -> "QlikParser":
        with open(self.payload_path, encoding="utf-8") as f:
            data = json.load(f)
        # Support both list format (new notebook) and dict format (old script)
        if isinstance(data, list):
            self.messages = data
        elif isinstance(data, dict):
            self.messages = data.get("requests", []) + data.get("responses", [])
        logger.info(f"Loaded {len(self.messages)} messages from {self.payload_path}")
        return self

    def extract_hypercube_data(self) -> "QlikParser":
        self.hypercube_responses = []
        for msg in self.messages:
            if msg.get("dir", msg.get("direction", "")) != "recv":
                continue
            pages = self._find_qdata_pages(msg.get("data", {}))
            if pages:
                self.hypercube_responses.append({"ts": msg.get("ts"), "pages": pages})
        logger.info(f"Found {len(self.hypercube_responses)} HyperCube responses")
        return self

    def _find_qdata_pages(self, obj: Any, depth: int = 0) -> list:
        if depth > 8: return []
        if isinstance(obj, dict):
            if "qDataPages" in obj: return obj["qDataPages"]
            for v in obj.values():
                r = self._find_qdata_pages(v, depth + 1)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r = self._find_qdata_pages(item, depth + 1)
                if r: return r
        return []

    def extract_matrix_rows(self) -> "QlikParser":
        self.raw_rows = []
        for hc in self.hypercube_responses:
            for page in hc.get("pages", []):
                for row in page.get("qMatrix", []):
                    self.raw_rows.append({"cells": row, "ts": hc.get("ts")})
        logger.info(f"Extracted {len(self.raw_rows)} matrix rows")
        return self

    def to_dataframe(self, column_labels: list[str] | None = None) -> pd.DataFrame:
        if not self.raw_rows:
            return pd.DataFrame()
        records = []
        for row_obj in self.raw_rows:
            record = {}
            for i, cell in enumerate(row_obj["cells"]):
                if isinstance(cell, dict):
                    name = column_labels[i] if column_labels and i < len(column_labels) else f"col_{i}"
                    record[name] = cell.get("qText", "")
                    record[f"{name}_num"] = cell.get("qNum")
            records.append(record)
        df = pd.DataFrame(records)
        logger.info(f"DataFrame: {df.shape[0]} rows × {df.shape[1]} cols")
        return df

    def parse_summary(self) -> str:
        methods = {}
        for msg in self.messages:
            d = msg.get("data", {})
            m = d.get("method") or d.get("params", {}).get("method", "") or ("result" if "result" in d else "other")
            methods[m] = methods.get(m, 0) + 1

        lines = [
            f"=== Qlik Parser Summary ===",
            f"Source: {self.payload_path}",
            f"Total messages: {len(self.messages)}",
            f"HyperCube responses: {len(self.hypercube_responses)}",
            f"Matrix rows: {len(self.raw_rows)}",
            "\nMessage types:",
        ] + [f"  {m}: {c}" for m, c in sorted(methods.items(), key=lambda x: -x[1])[:15]]

        if self.raw_rows:
            sample = self.raw_rows[0]["cells"]
            lines.append(f"\nFirst row ({len(sample)} columns):")
            for i, cell in enumerate(sample[:10]):
                if isinstance(cell, dict):
                    lines.append(f"  [{i}] qText={cell.get('qText','')!r:30}  qNum={cell.get('qNum','')}")
        return "\n".join(lines)


def parse_ws_payload(
    payload_path: str | Path,
    column_labels: list[str] | None = None,
    data_month: str | None = None,
) -> pd.DataFrame:
    parser = QlikParser(payload_path)
    parser.load().extract_hypercube_data().extract_matrix_rows()
    print(parser.parse_summary())

    if not parser.raw_rows:
        print("\n⚠️  No matrix rows found — SkillSelect may use REST API not WebSocket")
        print("   → Check the network_log file or HAR file for JSON responses")
        return pd.DataFrame()

    df = parser.to_dataframe(column_labels)
    df["data_month"]   = data_month or datetime.now().strftime("%Y-%m")
    df["extracted_at"] = datetime.now().isoformat()
    df["source_url"]   = "https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect/invitation-rounds"
    return df


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ETL/skillselect_qlik_parser.py captures/ws_payload_*.json")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = parse_ws_payload(sys.argv[1])
    if not df.empty:
        out = Path("captures") / "parsed_preview.csv"
        df.to_csv(out, index=False)
        print(f"\n✅ {df.shape} → {out}")
