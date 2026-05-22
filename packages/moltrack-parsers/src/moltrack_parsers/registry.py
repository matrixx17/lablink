from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Tuple

from moltrack_parsers.models import FileType, ParseResult, base_result
from moltrack_parsers.parsers import csv, dft, gromacs_edr, sdf, vina

Parser = Tuple[str, Callable[[str, bytes], bool], Callable[[str], ParseResult]]

_PARSERS: List[Parser] = [
    ("gromacs_edr", gromacs_edr.detect, gromacs_edr.parse),
    ("dft_log", dft.detect, dft.parse),
    ("vina_log", vina.detect, vina.parse),
    ("sdf", sdf.detect, sdf.parse),
    ("csv", csv.detect, csv.parse),
]


def parse_file(filepath: str) -> ParseResult:
    path = Path(filepath)
    head = path.read_bytes()[:8192]
    for _, detect, parse in _PARSERS:
        try:
            if detect(filepath, head):
                return parse(filepath)
        except Exception:
            continue
    return base_result(filepath, FileType.UNKNOWN, None)


def registered_parsers() -> List[str]:
    return [name for name, _, _ in _PARSERS]
