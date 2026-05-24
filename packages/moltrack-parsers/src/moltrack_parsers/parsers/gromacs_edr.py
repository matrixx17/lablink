from __future__ import annotations

from pathlib import Path

from moltrack_parsers.models import FileType, MetricValue, base_result


def detect(filepath: str, head: bytes) -> bool:
    return Path(filepath).suffix.lower() == ".edr"


def parse(filepath: str):
    result = base_result(filepath, FileType.GROMACS_EDR, "GROMACS")
    result.raw_metadata["artifact_kind"] = "energy"
    try:
        from MDAnalysis.auxiliary.EDR import EDRReader  # type: ignore

        reader = EDRReader(filepath)
        terms = list(getattr(reader, "terms", []) or [])
        result.raw_metadata["energy_terms"] = terms

        # Keep this intentionally light: EDR files can be large, and the edge
        # agent should not materialize every frame just to register provenance.
        for preferred in ("Potential", "Total Energy", "Temperature", "Pressure"):
            if preferred in terms:
                values = []
                for step in reader:
                    try:
                        values.append(float(step.data[preferred]))
                    except Exception:
                        continue
                    if len(values) >= 10000:
                        break
                if values:
                    result.extracted_metrics[f"{preferred.lower().replace(' ', '_')}_mean"] = MetricValue(
                        sum(values) / len(values),
                        "native",
                        metadata={"n": len(values), "min": min(values), "max": max(values)},
                    )
    except Exception as e:
        result.raw_metadata["mdanalysis_error"] = str(e)
    return result
