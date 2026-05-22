from __future__ import annotations

import re
from pathlib import Path

from moltrack_parsers.models import FileType, MetricValue, base_result

_GAUSSIAN_RE = re.compile(r"Gaussian\s+\d+|Entering Gaussian System", re.I)
_ORCA_RE = re.compile(r"\bO\s*R\s*C\s*A\b|Program Version\s+\d+\.\d+", re.I)
_ENERGY_RE = re.compile(r"SCF Done:\s+E\([^)]+\)\s+=\s+(-?\d+\.\d+)|FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", re.I)


def detect(filepath: str, head: bytes) -> bool:
    if Path(filepath).suffix.lower() not in {".log", ".out"}:
        return False
    text = head.decode(errors="ignore")
    return bool(_GAUSSIAN_RE.search(text) or _ORCA_RE.search(text))


def parse(filepath: str):
    text = Path(filepath).read_text(errors="ignore")
    software = "ORCA" if _ORCA_RE.search(text[:20000]) else "Gaussian"
    result = base_result(filepath, FileType.DFT_LOG, software)

    try:
        import cclib  # type: ignore
        data = cclib.io.ccread(filepath)
        if data is not None:
            if hasattr(data, "metadata"):
                result.raw_metadata.update(dict(data.metadata or {}))
            if hasattr(data, "scfenergies") and len(data.scfenergies):
                # cclib energies are eV
                result.extracted_metrics["final_scf_energy"] = MetricValue(
                    float(data.scfenergies[-1]),
                    "eV",
                    metadata={"source": "cclib"},
                )
            if hasattr(data, "scfvalues"):
                result.raw_metadata["scf_cycles"] = len(data.scfvalues)
            if hasattr(data, "vibfreqs"):
                freqs = [float(x) for x in data.vibfreqs]
                result.raw_metadata["imaginary_frequency_count"] = sum(1 for f in freqs if f < 0)
    except Exception as e:
        result.raw_metadata["cclib_error"] = str(e)

    if "final_scf_energy" not in result.extracted_metrics:
        matches = list(_ENERGY_RE.finditer(text))
        if matches:
            val = matches[-1].group(1) or matches[-1].group(2)
            result.extracted_metrics["final_scf_energy"] = MetricValue(float(val), "Hartree", metadata={"source": "regex"})

    result.raw_metadata["normal_termination"] = "Normal termination" in text or "ORCA TERMINATED NORMALLY" in text
    return result
