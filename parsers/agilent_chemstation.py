"""
Agilent ChemStation HPLC data parser.

Parses data from Agilent ChemStation software, which is one of the most
common HPLC/GC data systems in analytical chemistry labs.

ChemStation data structure (.D folders):
    sample.D/
    ├── acq.txt or ACQ.TXT    - Acquisition parameters
    ├── *.ch                   - Binary chromatogram files
    ├── RESULTS.CSV            - Integrated peak results
    ├── sequence.acaml         - XML sequence info (optional)
    └── DA.M/                  - Method subfolder (optional)

This parser focuses on text/CSV files. Binary .ch parsing is not
implemented (would require reverse-engineering the binary format).
"""

import os
import re
import csv
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from .base import BaseParser, ParsedResult


class AgilentChemStationParser(BaseParser):
    """
    Parser for Agilent ChemStation HPLC/GC data.

    Handles .D data folders containing acquisition parameters,
    peak results, and chromatogram metadata.
    """

    name = "agilent_chemstation"
    description = "Agilent ChemStation HPLC/GC data (.D folders)"
    vendor = "Agilent Technologies"

    # Files to look for in .D folder (case-insensitive)
    ACQ_FILES = ["acq.txt", "ACQ.TXT", "Acq.txt"]
    RESULTS_FILES = ["RESULTS.CSV", "results.csv", "Results.csv", "REPORT.CSV", "report.csv"]
    SEQUENCE_FILES = ["sequence.acaml", "SEQUENCE.ACAML"]

    def supported_extensions(self) -> List[str]:
        """ChemStation uses .D folders (directories)."""
        return [".d", ".D"]

    def detect(self, file_path: str) -> bool:
        """
        Detect if this is a ChemStation .D data folder.

        Criteria:
        1. Path ends with .D or .d
        2. Is a directory
        3. Contains acq.txt OR .ch files
        """
        path = Path(file_path)

        # Check extension
        if path.suffix.lower() != ".d":
            return False

        # Must be a directory
        if not path.is_dir():
            return False

        # Look for characteristic files
        has_acq = any(
            (path / f).exists() for f in self.ACQ_FILES
        )
        has_ch_files = any(
            f.suffix.lower() == ".ch" for f in path.iterdir() if f.is_file()
        )
        has_results = any(
            (path / f).exists() for f in self.RESULTS_FILES
        )

        # Must have at least acq.txt or .ch files
        return has_acq or has_ch_files or has_results

    def parse(self, file_path: str) -> ParsedResult:
        """
        Parse a ChemStation .D data folder.

        Extracts:
        - Acquisition parameters from acq.txt
        - Peak results from RESULTS.CSV
        - File inventory for reference
        """
        path = Path(file_path)

        if not path.is_dir():
            raise ValueError(f"Expected a directory, got: {file_path}")

        warnings = []
        metadata = {}

        # Get folder size
        folder_size = sum(
            f.stat().st_size for f in path.rglob("*") if f.is_file()
        )

        # Parse acquisition file
        acq_data = self._parse_acq_file(path)
        if acq_data:
            metadata.update(acq_data)
        else:
            warnings.append("acq.txt not found or could not be parsed")

        # Parse results file
        results_df, results_warnings = self._parse_results_file(path)
        warnings.extend(results_warnings)

        # Inventory .ch files (chromatogram data)
        ch_files = self._inventory_ch_files(path)
        if ch_files:
            metadata["chromatogram_files"] = ch_files
            metadata["chromatogram_count"] = len(ch_files)
        else:
            warnings.append("No .ch chromatogram files found")

        # Parse sequence file if present
        sequence_data = self._parse_sequence_file(path)
        if sequence_data:
            metadata.update(sequence_data)

        # Check for method folder
        method_info = self._check_method_folder(path)
        if method_info:
            metadata.update(method_info)

        # Extract timestamp
        timestamp = self._extract_timestamp(metadata, path)

        # Compute stats if we have results
        raw_stats = {}
        headers = []
        if results_df is not None and not results_df.empty:
            headers = list(results_df.columns)
            raw_stats = self.compute_column_stats(results_df)

        return ParsedResult(
            instrument="agilent_chemstation",
            format_version=metadata.get("chemstation_version", "unknown"),
            timestamp=timestamp,
            metadata=metadata,
            headers=headers,
            data=results_df,
            raw_stats=raw_stats,
            parse_warnings=warnings,
            source_file=path.name,
            file_size_bytes=folder_size,
        )

    def _parse_acq_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        Parse acquisition parameters from acq.txt.

        This file contains key-value pairs with acquisition metadata.
        Format varies by ChemStation version but typically includes:
        - Sample Name
        - Operator
        - Acq. Method
        - Injection Date
        - Vial Number
        """
        acq_file = None
        for fname in self.ACQ_FILES:
            candidate = path / fname
            if candidate.exists():
                acq_file = candidate
                break

        if not acq_file:
            return None

        metadata = {}

        try:
            # Try different encodings (ChemStation varies)
            content = None
            for encoding in ["utf-8", "latin-1", "cp1252"]:
                try:
                    with open(acq_file, "r", encoding=encoding) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue

            if not content:
                return None

            # Parse key-value pairs
            # Common formats:
            # "Key : Value"
            # "Key: Value"
            # "Key    Value" (tab separated)

            patterns = [
                # Standard colon-separated
                (r"Sample\s*(?:Name)?[:\s]+(.+)", "sample_name"),
                (r"Operator[:\s]+(.+)", "operator"),
                (r"Acq\.?\s*Method[:\s]+(.+)", "method"),
                (r"Injection\s*(?:Date|Time)?[:\s]+(.+)", "injection_datetime"),
                (r"Inj(?:ection)?\s*Volume[:\s]+(\d+\.?\d*)", "injection_volume"),
                (r"Vial[:\s]+(\d+)", "vial"),
                (r"Inj(?:ection)?[:\s]+(\d+)", "injection_number"),
                (r"Seq(?:uence)?\.?\s*Line[:\s]+(\d+)", "sequence_line"),
                (r"Location[:\s]+(.+)", "location"),
                (r"Instrument[:\s]+(.+)", "instrument_name"),
                (r"(?:Chem\s*Station|Software)\s*(?:Version)?[:\s]+(.+)", "chemstation_version"),
            ]

            for pattern, key in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    if value:
                        metadata[key] = value

            # Try to parse injection datetime
            if "injection_datetime" in metadata:
                metadata["injection_datetime_parsed"] = self._parse_datetime(
                    metadata["injection_datetime"]
                )

        except Exception as e:
            metadata["acq_parse_error"] = str(e)

        return metadata

    def _parse_results_file(self, path: Path) -> Tuple[Optional[pd.DataFrame], List[str]]:
        """
        Parse peak results from RESULTS.CSV or similar.

        Returns the peak table as a DataFrame with columns like:
        - Peak Number
        - Retention Time (RT)
        - Area
        - Height
        - Concentration (if calibrated)
        - Compound Name (if identified)
        """
        warnings = []
        results_file = None

        for fname in self.RESULTS_FILES:
            candidate = path / fname
            if candidate.exists():
                results_file = candidate
                break

        if not results_file:
            # Look for any CSV file
            csv_files = list(path.glob("*.csv")) + list(path.glob("*.CSV"))
            if csv_files:
                results_file = csv_files[0]
                warnings.append(f"Using {results_file.name} as results file")

        if not results_file:
            warnings.append("No results CSV found in .D folder")
            return None, warnings

        try:
            # ChemStation CSVs can have varying formats
            # Try to detect delimiter and header row

            # Read first few lines to detect format
            with open(results_file, "r", encoding="latin-1") as f:
                first_lines = [f.readline() for _ in range(20)]

            # Find header row (look for lines with multiple commas AND keywords)
            header_row = 0
            for i, line in enumerate(first_lines):
                line_lower = line.lower()
                # Header row typically has Peak AND (Time OR Area) AND multiple commas
                has_keywords = (
                    ("peak" in line_lower or "rt" in line_lower) and
                    any(kw in line_lower for kw in ["time", "area", "height", "width"])
                )
                has_delimiters = line.count(",") >= 3 or line.count("\t") >= 3
                if has_keywords and has_delimiters:
                    header_row = i
                    break

            # Detect delimiter from header row
            delimiter = ","
            if header_row < len(first_lines):
                header_line = first_lines[header_row]
                if header_line.count("\t") > header_line.count(","):
                    delimiter = "\t"
                elif header_line.count(";") > header_line.count(","):
                    delimiter = ";"

            # Parse CSV
            df = pd.read_csv(
                results_file,
                delimiter=delimiter,
                encoding="latin-1",
                skiprows=header_row,
                on_bad_lines="skip",
            )

            # Clean up column names
            df.columns = [self._normalize_column_name(col) for col in df.columns]

            # Filter to relevant columns if too many
            relevant_cols = []
            for col in df.columns:
                col_lower = col.lower()
                if any(kw in col_lower for kw in [
                    "peak", "rt", "time", "area", "height", "conc", "amount",
                    "compound", "name", "width", "symmetry", "plate", "type"
                ]):
                    relevant_cols.append(col)

            if relevant_cols and len(relevant_cols) < len(df.columns):
                df = df[relevant_cols]

            # Remove empty rows
            df = df.dropna(how="all")

            # Remove summary/totals rows (e.g., "Totals:" in first column)
            first_col = df.columns[0]
            df = df[~df[first_col].astype(str).str.lower().str.contains("total", na=False)]

            return df, warnings

        except Exception as e:
            warnings.append(f"Error parsing results CSV: {e}")
            return None, warnings

    def _inventory_ch_files(self, path: Path) -> List[Dict[str, Any]]:
        """
        Inventory .ch chromatogram files.

        We don't parse the binary format, but we record what's available.
        Common .ch files:
        - DAD1A.ch - Diode array detector signal A
        - FID1A.ch - Flame ionization detector
        - etc.
        """
        ch_files = []

        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() == ".ch":
                ch_files.append({
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                    "detector_hint": self._guess_detector_from_filename(f.name),
                })

        return ch_files

    def _guess_detector_from_filename(self, filename: str) -> str:
        """Guess detector type from .ch filename."""
        name = filename.upper()

        if "DAD" in name:
            return "Diode Array Detector"
        elif "FID" in name:
            return "Flame Ionization Detector"
        elif "MSD" in name or "MS" in name:
            return "Mass Spectrometer"
        elif "RID" in name:
            return "Refractive Index Detector"
        elif "FLD" in name:
            return "Fluorescence Detector"
        elif "ECD" in name:
            return "Electron Capture Detector"
        elif "TCD" in name:
            return "Thermal Conductivity Detector"
        elif "VWD" in name:
            return "Variable Wavelength Detector"
        else:
            return "Unknown Detector"

    def _parse_sequence_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        Parse sequence information from sequence.acaml (XML format).

        This file contains the overall sequence/batch information.
        """
        seq_file = None
        for fname in self.SEQUENCE_FILES:
            candidate = path / fname
            if candidate.exists():
                seq_file = candidate
                break

        if not seq_file:
            return None

        try:
            # Basic XML parsing - just extract key elements
            with open(seq_file, "r", encoding="utf-8") as f:
                content = f.read()

            metadata = {}

            # Look for common elements
            patterns = [
                (r"<Name>([^<]+)</Name>", "sequence_name"),
                (r"<Description>([^<]+)</Description>", "sequence_description"),
                (r"<Operator>([^<]+)</Operator>", "sequence_operator"),
            ]

            for pattern, key in patterns:
                match = re.search(pattern, content)
                if match:
                    metadata[key] = match.group(1).strip()

            return metadata if metadata else None

        except Exception:
            return None

    def _check_method_folder(self, path: Path) -> Optional[Dict[str, Any]]:
        """Check for method subfolder and extract basic info."""
        method_folders = ["DA.M", "da.m", "Method"]

        for folder_name in method_folders:
            method_path = path / folder_name
            if method_path.is_dir():
                # Count files in method folder
                method_files = list(method_path.iterdir())
                return {
                    "method_folder": folder_name,
                    "method_file_count": len(method_files),
                }

        return None

    def _extract_timestamp(
        self,
        metadata: Dict[str, Any],
        path: Path
    ) -> Optional[datetime]:
        """Extract the best available timestamp."""
        # Try parsed injection datetime first
        if "injection_datetime_parsed" in metadata:
            return metadata["injection_datetime_parsed"]

        # Fall back to folder modification time
        try:
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except Exception:
            return None

    def _parse_datetime(self, date_str: str) -> Optional[datetime]:
        """Parse various ChemStation datetime formats."""
        formats = [
            "%d-%b-%y, %H:%M:%S",      # 15-Jan-24, 10:30:00
            "%d-%b-%Y, %H:%M:%S",      # 15-Jan-2024, 10:30:00
            "%m/%d/%Y %H:%M:%S",       # 01/15/2024 10:30:00
            "%m/%d/%y %H:%M:%S",       # 01/15/24 10:30:00
            "%Y-%m-%d %H:%M:%S",       # 2024-01-15 10:30:00
            "%d.%m.%Y %H:%M:%S",       # 15.01.2024 10:30:00
            "%d %b %Y %H:%M",          # 15 Jan 2024 10:30
            "%b %d, %Y %H:%M:%S",      # Jan 15, 2024 10:30:00
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue

        return None

    def _normalize_column_name(self, col: str) -> str:
        """Normalize column names from results file."""
        if not isinstance(col, str):
            return str(col)

        # Clean up whitespace
        col = col.strip()
        col = re.sub(r"\s+", " ", col)

        # Common normalizations
        replacements = {
            "Ret. Time": "Retention_Time",
            "Ret.Time": "Retention_Time",
            "RT": "Retention_Time",
            "R.T.": "Retention_Time",
        }

        for old, new in replacements.items():
            if col.lower() == old.lower():
                return new

        return col
