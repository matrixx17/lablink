"""
Computational chemistry parser registry.

Plugin-style architecture mirroring the lab-instrument parser registry.
Parsers are checked in priority order; the first one whose detect()
returns True is used. There is no universal fallback — if no parser
matches, the file is returned as a CompChemParsedResult with
software_name="unknown" and run_kind="other".

Order matters here too:
  - DFT log parsers first (cclib does heavy lifting, very specific)
  - Docking outputs (Glide .maegz, Vina .pdbqt)
  - MD trajectory + control files (GROMACS, OpenMM)
  - Property tables (RDKit SDF/CSV) last — most generic
"""

import os
from typing import List, Optional, Type

from .base import CompChemParser, CompChemParsedResult, RunKind, TerminationStatus
from .gaussian_orca import GaussianParser, ORCAParser
from .glide import GlideParser
from .vina_gnina import VinaParser, GninaParser
from .gromacs import GROMACSParser
from .openmm import OpenMMParser
from .rdkit_table import RDKitTableParser

_COMPCHEM_REGISTRY: List[Type[CompChemParser]] = [
    GaussianParser,
    ORCAParser,
    GlideParser,
    VinaParser,
    GninaParser,
    GROMACSParser,
    OpenMMParser,
    RDKitTableParser,
]

_parser_instances: Optional[List[CompChemParser]] = None


def _get_parsers() -> List[CompChemParser]:
    global _parser_instances
    if _parser_instances is None:
        _parser_instances = [cls() for cls in _COMPCHEM_REGISTRY]
    return _parser_instances


def detect_compchem_format(file_path: str) -> Optional[str]:
    """
    Identify which comp-chem tool produced this file.

    Returns the parser name, or None if no parser claims it.
    """
    if not os.path.exists(file_path):
        return None
    for parser in _get_parsers():
        try:
            if parser.detect(file_path):
                return parser.name
        except Exception:
            continue
    return None


def parse_compchem_file(
    file_path: str,
    format_hint: Optional[str] = None,
) -> CompChemParsedResult:
    """
    Parse a comp-chem file and return a standardised result.

    If no parser claims the file, returns a minimal result with
    software_name="unknown" rather than raising — the agent should
    still upload the raw bytes for forensic value.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Path not found: {file_path}")

    parsers = _get_parsers()

    if format_hint:
        for parser in parsers:
            if parser.name == format_hint:
                return parser.parse(file_path)
        raise ValueError(
            f"Unknown comp-chem parser: {format_hint}. "
            f"Available: {[p.name for p in parsers]}"
        )

    for parser in parsers:
        try:
            if parser.detect(file_path):
                return parser.parse(file_path)
        except Exception:
            continue

    # Unknown file — return a stub result; agent will still upload raw bytes
    return CompChemParsedResult(
        software_name="unknown",
        software_version=None,
        run_kind=RunKind.OTHER,
        termination_status=TerminationStatus.UNKNOWN,
        source_file=file_path,
        file_size_bytes=os.path.getsize(file_path),
    )


def list_compchem_formats() -> List[dict]:
    return [
        {
            "name": p.name,
            "software": p.software_name,
            "run_kinds": [k.value for k in p.run_kinds],
            "extensions": p.supported_extensions(),
        }
        for p in _get_parsers()
    ]


__all__ = [
    "detect_compchem_format",
    "parse_compchem_file",
    "list_compchem_formats",
    "CompChemParser",
    "CompChemParsedResult",
    "RunKind",
    "TerminationStatus",
]
