"""
Generic CSV parser with intelligent format detection.

This is the fallback parser that handles any CSV-like file with:
- Auto-detection of delimiter (comma, tab, semicolon, pipe)
- Header row detection
- Encoding detection
- Numeric column identification and statistics
"""

import os
import csv
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

import pandas as pd
import numpy as np

from .base import BaseParser, ParsedResult
from .wetlab.assay_format import detect_assay_format, detect_plate_metadata
from .wetlab.assay_qc import AssayQCEngine


class GenericCSVParser(BaseParser):
    """
    Generic CSV parser that handles various CSV dialects.

    Features:
    - Delimiter auto-detection (comma, tab, semicolon, pipe)
    - Encoding detection with fallbacks
    - Header row detection
    - Handles files with or without headers
    - Computes statistics for numeric columns
    """

    name = "generic_csv"
    description = "Generic CSV/TSV parser with auto-detection"
    vendor = "generic"

    # Common delimiters in order of preference
    DELIMITERS = [",", "\t", ";", "|"]

    # Common encodings to try
    ENCODINGS = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".txt", ".dat", ".xlsx", ".xls"]

    def detect(self, file_path: str) -> bool:
        """
        Detect if this parser can handle the file.

        This parser is the fallback, so it accepts most text files
        that look like delimited data.
        """
        if not os.path.isfile(file_path):
            return False

        ext = os.path.splitext(file_path)[1].lower()

        # Accept known extensions
        if ext in self.supported_extensions():
            return True

        # Try to detect if it's a text file with delimited data
        try:
            content = self._read_sample(file_path, max_bytes=4096)
            if content and self._looks_like_csv(content):
                return True
        except Exception:
            pass

        return False

    def parse(self, file_path: str) -> ParsedResult:
        """Parse a CSV file and return standardized result."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        warnings = []
        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        if ext in (".xlsx", ".xls"):
            encoding = "utf-8"
            has_header = True
            try:
                df = pd.read_excel(file_path)
            except Exception as e:
                raise ValueError(f"Failed to parse Excel file: {e}")
        else:
            # Detect encoding
            encoding = self._detect_encoding(file_path)
            if encoding != "utf-8":
                warnings.append(f"Non-UTF-8 encoding detected: {encoding}")

            # Read sample to detect format
            sample = self._read_sample(file_path, encoding=encoding)

            # Detect delimiter
            delimiter = self._detect_delimiter(sample)
            if delimiter != ",":
                warnings.append(f"Non-standard delimiter detected: {repr(delimiter)}")

            # Detect if file has header
            has_header, header_confidence = self._detect_header(sample, delimiter)

            # Detect comment character
            comment_char = self._detect_comment_char(sample)

            # Parse with pandas
            try:
                df = pd.read_csv(
                    file_path,
                    delimiter=delimiter,
                    encoding=encoding,
                    header=0 if has_header else None,
                    comment=comment_char,
                    on_bad_lines="warn",
                    engine="python",  # More flexible parsing
                )
            except Exception as e:
                raise ValueError(f"Failed to parse CSV: {e}")

        # If no header, generate column names
        if not has_header:
            df.columns = [f"column_{i+1}" for i in range(len(df.columns))]
            warnings.append("No header row detected; generated column names")

        # Clean column names
        original_columns = list(df.columns)
        df.columns = self._clean_column_names(df.columns)

        if list(df.columns) != original_columns:
            warnings.append("Column names were cleaned/normalized")

        # Compute statistics for numeric columns
        raw_stats = self.compute_column_stats(df)

        # Extract any metadata from the file (comments, etc.)
        metadata = {} if ext in (".xlsx", ".xls") else self._extract_metadata(file_path, encoding)

        try:
            assay_format = detect_assay_format(df, list(df.columns))
            metadata["assay_format"] = assay_format
            metadata.update(detect_plate_metadata(df, list(df.columns)))
            if assay_format != "unknown":
                metadata["assay_qc"] = [
                    result.to_dict()
                    for result in AssayQCEngine().run(df, assay_format)
                ]
        except Exception as e:
            warnings.append(f"Assay QC detection failed: {e}")

        # Try to detect timestamp from file or metadata
        timestamp = self._detect_timestamp(file_path, metadata)

        return ParsedResult(
            instrument="generic_csv",
            format_version="1.0",
            timestamp=timestamp,
            metadata=metadata,
            headers=list(df.columns),
            data=df,
            raw_stats=raw_stats,
            parse_warnings=warnings,
            source_file=os.path.basename(file_path),
            file_size_bytes=file_size,
        )

    def _read_sample(
        self,
        file_path: str,
        max_bytes: int = 8192,
        encoding: str = "utf-8"
    ) -> str:
        """Read a sample of the file for format detection."""
        for enc in ([encoding] + self.ENCODINGS):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read(max_bytes)
            except UnicodeDecodeError:
                continue
            except Exception:
                break

        # Fallback: read with errors ignored
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_bytes)

    def _detect_encoding(self, file_path: str) -> str:
        """Detect file encoding by trying common encodings."""
        # Read raw bytes for BOM detection
        with open(file_path, "rb") as f:
            raw = f.read(4)

        # Check for BOM
        if raw.startswith(b'\xef\xbb\xbf'):
            return "utf-8-sig"
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            return "utf-16"

        # Try each encoding
        for enc in self.ENCODINGS:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    f.read(8192)  # Read a chunk to validate
                return enc
            except UnicodeDecodeError:
                continue

        return "utf-8"  # Default fallback

    def _detect_delimiter(self, sample: str) -> str:
        """Detect the most likely delimiter in the sample."""
        if not sample:
            return ","

        # Split into lines
        lines = sample.strip().split("\n")
        if len(lines) < 2:
            return ","

        # Count delimiter occurrences per line
        delimiter_counts = {d: [] for d in self.DELIMITERS}

        for line in lines[:10]:  # Check first 10 lines
            for delim in self.DELIMITERS:
                count = line.count(delim)
                delimiter_counts[delim].append(count)

        # Find delimiter with most consistent non-zero count
        best_delimiter = ","
        best_score = 0

        for delim, counts in delimiter_counts.items():
            if not counts or all(c == 0 for c in counts):
                continue

            # Score based on consistency and frequency
            avg_count = sum(counts) / len(counts)
            if avg_count == 0:
                continue

            # Variance as measure of consistency
            variance = sum((c - avg_count) ** 2 for c in counts) / len(counts)
            consistency = 1 / (1 + variance)

            score = avg_count * consistency

            if score > best_score:
                best_score = score
                best_delimiter = delim

        return best_delimiter

    def _detect_header(
        self,
        sample: str,
        delimiter: str
    ) -> Tuple[bool, float]:
        """
        Detect if the first row is a header.

        Returns:
            Tuple of (has_header, confidence)
        """
        if not sample:
            return True, 0.5

        lines = sample.strip().split("\n")
        if len(lines) < 2:
            return True, 0.5

        # Parse first two rows
        try:
            first_row = next(csv.reader([lines[0]], delimiter=delimiter))
            second_row = next(csv.reader([lines[1]], delimiter=delimiter))
        except Exception:
            return True, 0.5

        if len(first_row) != len(second_row):
            return True, 0.5

        # Heuristics for header detection
        score = 0
        total = len(first_row)

        for h, d in zip(first_row, second_row):
            h_str = str(h).strip()
            d_str = str(d).strip()

            # Header is text, data is numeric
            h_numeric = self._looks_numeric(h_str)
            d_numeric = self._looks_numeric(d_str)

            if not h_numeric and d_numeric:
                score += 1
            elif h_numeric and d_numeric:
                score -= 0.5  # Both numeric suggests no header

            # Header contains common header words
            header_words = ["id", "name", "time", "date", "value", "sample",
                           "result", "temp", "conc", "area", "height"]
            if any(word in h_str.lower() for word in header_words):
                score += 0.5

        confidence = (score / total) if total > 0 else 0.5
        has_header = confidence > 0.3

        return has_header, min(1.0, max(0.0, confidence))

    def _looks_numeric(self, value: str) -> bool:
        """Check if a string looks like a number."""
        if not value:
            return False

        # Remove common number formatting
        cleaned = value.replace(",", "").replace(" ", "")

        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    def _looks_like_csv(self, content: str) -> bool:
        """Check if content looks like CSV data."""
        lines = content.strip().split("\n")
        if len(lines) < 2:
            return False

        # Check if lines have consistent delimiter counts
        for delim in self.DELIMITERS:
            counts = [line.count(delim) for line in lines[:5]]
            if counts[0] > 0 and len(set(counts)) <= 2:
                return True

        return False

    def _detect_comment_char(self, sample: str) -> Optional[str]:
        """Detect if file uses comment lines (starting with # or //)."""
        if not sample:
            return None

        lines = sample.strip().split("\n")

        # Check first few lines for comment patterns
        comment_chars = {"#": 0, ";": 0}  # Semicolon can also be comment in some formats

        for line in lines[:10]:
            stripped = line.strip()
            if stripped.startswith("#"):
                comment_chars["#"] += 1
            # Don't use semicolon as comment if it's likely a delimiter
            elif stripped.startswith(";") and stripped.count(";") == 1:
                comment_chars[";"] += 1

        # If we found comment lines with #, use it
        if comment_chars["#"] > 0:
            return "#"

        return None

    def _clean_column_names(self, columns: pd.Index) -> List[str]:
        """Clean and normalize column names."""
        cleaned = []
        seen = Counter()

        for col in columns:
            # Convert to string
            name = str(col).strip()

            # Remove problematic characters
            name = re.sub(r'[\r\n\t]', ' ', name)
            name = re.sub(r'\s+', ' ', name)
            name = name.strip()

            # Handle empty names
            if not name:
                name = "unnamed"

            # Handle duplicates
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0

            cleaned.append(name)

        return cleaned

    def _extract_metadata(
        self,
        file_path: str,
        encoding: str
    ) -> Dict[str, Any]:
        """
        Extract metadata from file comments or headers.

        Many lab files have metadata in comment lines at the top.
        """
        metadata = {}

        try:
            with open(file_path, "r", encoding=encoding) as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= 20:  # Only check first 20 lines
                        break
                    lines.append(line)

            # Look for comment lines (starting with #, //, or containing :)
            comment_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    comment_lines.append(stripped.lstrip("#/ "))
                elif ":" in stripped and not "," in stripped.split(":")[0]:
                    # Might be key: value format
                    parts = stripped.split(":", 1)
                    if len(parts) == 2 and len(parts[0]) < 30:
                        key = parts[0].strip().lower().replace(" ", "_")
                        value = parts[1].strip()
                        if key and value:
                            metadata[key] = value

            if comment_lines:
                metadata["file_comments"] = comment_lines

        except Exception:
            pass

        return metadata

    def _detect_timestamp(
        self,
        file_path: str,
        metadata: Dict[str, Any]
    ) -> Optional[datetime]:
        """Try to determine when the data was acquired."""
        # Check metadata for date fields
        date_keys = ["date", "acquisition_date", "run_date", "created", "timestamp"]
        for key in date_keys:
            if key in metadata:
                try:
                    return self._parse_datetime(metadata[key])
                except Exception:
                    pass

        # Fall back to file modification time
        try:
            mtime = os.path.getmtime(file_path)
            return datetime.fromtimestamp(mtime)
        except Exception:
            return None

    def _parse_datetime(self, value: str) -> datetime:
        """Parse various datetime formats."""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue

        raise ValueError(f"Cannot parse datetime: {value}")
