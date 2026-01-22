"""
Base classes and data structures for instrument file parsers.

This module provides the foundation for building parsers that handle
various laboratory instrument output formats.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
import pandas as pd


@dataclass
class ParsedResult:
    """
    Standardized result from parsing an instrument file.

    All parsers should return this structure to ensure consistent
    downstream processing regardless of the original file format.
    """
    # Instrument identification
    instrument: str  # e.g., "agilent_hplc", "tecan_reader", "generic_csv"
    format_version: str  # Parser or format version, e.g., "1.0", "ChemStation_B.04"

    # Temporal info
    timestamp: Optional[datetime]  # When the data was acquired (if available)

    # Metadata extracted from the file
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Common metadata keys:
    # - method: str (method/protocol name)
    # - operator: str (user who ran the instrument)
    # - instrument_id: str (serial number or asset ID)
    # - sample_id: str (sample identifier)
    # - batch_id: str (batch/run identifier)
    # - software_version: str
    # - acquisition_time: datetime
    # - comments: str

    # Parsed data
    headers: List[str] = field(default_factory=list)
    data: Optional[pd.DataFrame] = None  # The actual data

    # Pre-computed statistics per column (for QC)
    raw_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Structure: {column_name: {"mean": x, "std": y, "min": z, "max": w, "values": [...]}}

    # Any issues encountered during parsing
    parse_warnings: List[str] = field(default_factory=list)

    # Source file info
    source_file: str = ""
    file_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "instrument": self.instrument,
            "format_version": self.format_version,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata,
            "headers": self.headers,
            "row_count": len(self.data) if self.data is not None else 0,
            "raw_stats": self.raw_stats,
            "parse_warnings": self.parse_warnings,
            "source_file": self.source_file,
            "file_size_bytes": self.file_size_bytes,
        }

    def get_stats_for_qc(self) -> Dict[str, Dict[str, Any]]:
        """
        Get stats in the format expected by qc_summary().

        Returns:
            Dict suitable for passing to qc.qc_summary()
        """
        return self.raw_stats


class BaseParser(ABC):
    """
    Abstract base class for instrument file parsers.

    To add support for a new instrument format:
    1. Create a new file in /parsers/ (e.g., agilent_hplc.py)
    2. Subclass BaseParser
    3. Implement detect(), parse(), and supported_extensions()
    4. Register the parser in __init__.py
    """

    # Parser identification
    name: str = "base"
    description: str = "Base parser (not for direct use)"
    vendor: str = "unknown"

    @abstractmethod
    def detect(self, file_path: str) -> bool:
        """
        Determine if this parser can handle the given file.

        This method should be fast - it may read headers or magic bytes
        but should not parse the entire file.

        Args:
            file_path: Path to the file to check

        Returns:
            True if this parser can handle the file, False otherwise
        """
        pass

    @abstractmethod
    def parse(self, file_path: str) -> ParsedResult:
        """
        Parse the file and return a standardized result.

        Args:
            file_path: Path to the file to parse

        Returns:
            ParsedResult with extracted data and metadata

        Raises:
            ValueError: If the file cannot be parsed
            FileNotFoundError: If the file doesn't exist
        """
        pass

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """
        Return list of file extensions this parser handles.

        Returns:
            List of extensions including the dot, e.g., [".csv", ".txt"]
        """
        pass

    def compute_column_stats(
        self,
        df: pd.DataFrame,
        max_values: int = 100
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compute statistics for numeric columns in a DataFrame.

        Args:
            df: DataFrame to analyze
            max_values: Maximum number of values to include in output

        Returns:
            Dict mapping column names to their statistics
        """
        stats = {}

        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                series = df[col].dropna()

                if len(series) == 0:
                    stats[col] = {
                        "mean": None,
                        "std": None,
                        "min": None,
                        "max": None,
                        "n": 0,
                        "null_count": len(df[col]),
                        "values": [],
                    }
                    continue

                series_float = series.astype(float)
                values_list = series_float.tolist()

                stats[col] = {
                    "mean": float(series_float.mean()),
                    "std": float(series_float.std(ddof=1)) if len(series_float) > 1 else 0.0,
                    "min": float(series_float.min()),
                    "max": float(series_float.max()),
                    "n": len(series_float),
                    "null_count": int(df[col].isna().sum()),
                    "values": values_list[:max_values],  # Cap for payload size
                }

        return stats

    def _read_file_safely(
        self,
        file_path: str,
        encoding: str = "utf-8"
    ) -> str:
        """
        Read file contents with encoding fallback.

        Args:
            file_path: Path to the file
            encoding: Primary encoding to try

        Returns:
            File contents as string
        """
        encodings = [encoding, "utf-8", "latin-1", "cp1252", "iso-8859-1"]

        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        # Last resort: read with errors ignored
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
