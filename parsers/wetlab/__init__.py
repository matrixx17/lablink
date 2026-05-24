"""
Wet lab fallback + niche bioreactor parsers.

The vendor-specific parsers live in `parsers/bioprocess_platform.py` and
match on column signatures. This package adds:

  - AktaCsvParser            — Cytiva ÄKTA chromatography exports
  - GenericBioprocessCsvParser — fallback for un-vendored controller CSVs
  - GenericOfflineSampleCsvParser — fallback for un-vendored offline CSVs

These are registered in `parsers/__init__.py` *after* the vendor-specific
parsers and *before* `GenericCSVParser`, so vendor parsers still win
when they match.
"""

from .akta_csv import AktaCsvParser
from .assay_format import detect_assay_format
from .assay_qc import AssayQCEngine
from .assay_table import AssayTableParser
from .canonical_names import canonicalize_parameter
from .generic_bioprocess_csv import GenericBioprocessCsvParser
from .generic_offline_sample_csv import GenericOfflineSampleCsvParser

__all__ = [
    "AktaCsvParser",
    "AssayQCEngine",
    "AssayTableParser",
    "GenericBioprocessCsvParser",
    "GenericOfflineSampleCsvParser",
    "canonicalize_parameter",
    "detect_assay_format",
]
