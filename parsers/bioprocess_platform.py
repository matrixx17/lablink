"""
Bioprocess instrument parsers — controller exports and offline analytics.

Each parser detects vendor-specific CSV/TSV column signatures, then delegates
parsing to the generic CSV engine with bioprocess metadata enrichment.
"""

import csv
import os
import re
from typing import List, Optional, Set

from .generic_csv import GenericCSVParser
from .base import ParsedResult


def _normalize_headers(headers: List[str]) -> Set[str]:
    return {re.sub(r"\s+", " ", h.strip().lower()) for h in headers}


def _read_headers(file_path: str, gparser: GenericCSVParser) -> List[str]:
    content = gparser._read_sample(file_path, max_bytes=8192)
    delimiter = gparser._detect_delimiter(content)
    for line in content.splitlines()[:8]:
        if not line.strip():
            continue
        row = next(csv.reader([line], delimiter=delimiter))
        if len(row) >= 2:
            return [c.strip() for c in row]
    return []


class BioprocessPlatformParser(GenericCSVParser):
    """Base for vendor CSV exports that share delimited time-series structure."""

    platform_key: str = "bioprocess"
    vendor_name: str = "unknown"
    required_header_sets: List[Set[str]] = []
    optional_time_headers: List[str] = ["time", "time [h]", "elapsed time", "timestamp"]
    default_data_kind: str = "continuous"

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions():
            return False
        try:
            g = GenericCSVParser()
            headers = _read_headers(file_path, g)
            if len(headers) < 2:
                return False
            norm = _normalize_headers(headers)
            for required in self.required_header_sets:
                if required.issubset(norm):
                    return True
        except Exception:
            return False
        return False

    def _find_time_column(self, headers: List[str]) -> Optional[str]:
        for h in headers:
            hl = h.lower()
            for pat in self.optional_time_headers:
                if pat in hl or hl == pat:
                    return h
        return None

    def parse(self, file_path: str) -> ParsedResult:
        base = super().parse(file_path)
        base.instrument = self.platform_key
        base.metadata.setdefault("platform", self.platform_key)
        base.metadata.setdefault("vendor", self.vendor_name)
        base.data_kind = self.default_data_kind

        time_col = self._find_time_column(base.headers)
        base.time_column = time_col

        if base.data is not None:
            base.series_points = self.build_series_points(
                base.data, time_column=time_col, max_points=50000
            )
            base.raw_stats = self.compute_column_stats(base.data, max_values=50000)

        run_id = (
            base.metadata.get("batch_id")
            or base.metadata.get("run_id")
            or base.metadata.get("bioreactor_id")
        )
        if run_id:
            base.metadata["run_external_id"] = str(run_id)

        return base


class SartoriusBiostatParser(BioprocessPlatformParser):
    name = "sartorius_biostat"
    description = "Sartorius BIOSTAT / Ambr bioreactor export"
    vendor = "Sartorius"
    platform_key = "sartorius_biostat"
    required_header_sets = [
        {"time [h]", "viable cells"},
        {"time [h]", "vcd"},
        {"time", "do", "ph"},
        {"time [h]", "do [%]", "ph"},
    ]


class SartoriusAmbrParser(BioprocessPlatformParser):
    name = "sartorius_ambr"
    description = "Sartorius Ambr multi-parallel bioreactor export"
    vendor = "Sartorius"
    platform_key = "sartorius_ambr"
    required_header_sets = [
        {"vessel", "time [h]", "vcd"},
        {"bioreactor", "time", "viable cell density"},
        {"ambr", "time [h]"},
    ]


class EppendorfBioFloParser(BioprocessPlatformParser):
    name = "eppendorf_bioflo"
    description = "Eppendorf BioFlo / DASGIP controller export"
    vendor = "Eppendorf"
    platform_key = "eppendorf_bioflo"
    required_header_sets = [
        {"time", "temp", "ph", "po2"},
        {"time [h]", "temperature", "ph", "do"},
        {"elapsed time", "ph", "do"},
        {"time", "temperature (c)", "ph", "dissolved oxygen"},
    ]


class CytivaBioreactorParser(BioprocessPlatformParser):
    name = "cytiva_bioreactor"
    description = "Cytiva Xcellerex / WAVE bioreactor export"
    vendor = "Cytiva"
    platform_key = "cytiva_bioreactor"
    required_header_sets = [
        {"time", "ph", "do", "temperature"},
        {"time [h]", "vcd", "glucose"},
        {"elapsed", "ph", "dissolved oxygen"},
    ]


class NovaBioProfileParser(BioprocessPlatformParser):
    name = "nova_bioprofile"
    description = "Nova BioProfile FLEX metabolite analyzer"
    vendor = "Nova Biomedical"
    platform_key = "nova_bioprofile"
    default_data_kind = "discrete_offline"
    required_header_sets = [
        {"sample id", "glucose", "lactate"},
        {"sample", "glucose (g/l)", "lactate (g/l)"},
        {"date/time", "glucose", "glutamine"},
        {"sample id", "glutamine", "ammonia"},
    ]


class BeckmanViCellParser(BioprocessPlatformParser):
    name = "beckman_vicell"
    description = "Beckman Coulter Vi-CELL cell counter export"
    vendor = "Beckman Coulter"
    platform_key = "beckman_vicell"
    default_data_kind = "discrete_offline"
    required_header_sets = [
        {"sample id", "viable cells", "viability"},
        {"sample id", "vcd", "viability (%)"},
        {"sample id", "total cells", "viable cells/ml"},
        {"sample", "viable cell density", "viability"},
    ]


class BioprocessOfflineParser(BioprocessPlatformParser):
    """Discrete offline samples: titer, viability timepoints (generic detection)."""

    name = "bioprocess_offline"
    description = "Generic bioprocess offline sample sheet (titer/VCD timepoints)"
    vendor = "generic"
    platform_key = "bioprocess_offline"
    default_data_kind = "discrete_offline"
    required_header_sets = [
        {"time [h]", "titer"},
        {"time [h]", "titer (g/l)"},
        {"time", "titer (g/l)"},
        {"sample time", "titer"},
        {"hours", "vcd"},
    ]
