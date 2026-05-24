"""
OpenMM output parsers.

OpenMM is a library, not a CLI tool — output formats depend on the user's
script. The most common artifacts:
  .pdb                  Topology + reference structure.
  .dcd                  Binary trajectory (CHARMM/NAMD format, OpenMM default).
  .h5 / .hdf5           Optional HDF5 trajectory (via MDTraj reporter).
  StateDataReporter CSV With columns like step, potentialEnergy, temperature.

We treat the StateDataReporter CSV as the primary metric source — it has
a clean schema. The .pdb/.dcd/.h5 files are uploaded as artifacts.

Detection: presence of OpenMM-specific column headers in CSV, or
extension-based for PDB/DCD/HDF5 (which we tag as binary artifacts since
OpenMM doesn't write a unique signature into them).
"""

import csv
import io
import os
from datetime import datetime
from typing import List

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)


# StateDataReporter columns (whichever the user enabled at construction):
# Step,Time (ps),Potential Energy (kJ/mole),Kinetic Energy (kJ/mole),
# Total Energy (kJ/mole),Temperature (K),Box Volume (nm^3),Density (g/mL),Speed (ns/day)
_OPENMM_STATEDATA_COLS = {
    "step",
    "time (ps)",
    "potential energy (kj/mole)",
    "kinetic energy (kj/mole)",
    "total energy (kj/mole)",
    "temperature (k)",
}


class OpenMMParser(CompChemParser):
    name = "openmm"
    software_name = "OpenMM"
    run_kinds = [RunKind.MOLECULAR_DYNAMICS]

    def supported_extensions(self) -> List[str]:
        return [".csv", ".pdb", ".dcd", ".h5", ".hdf5"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            head = self._read_head(file_path, max_bytes=4096)
            first_line = head.split("\n", 1)[0].strip().lstrip('"').lstrip("#")
            cols = [c.strip().lower().strip('"') for c in first_line.split(",")]
            # Need at least 3 OpenMM-distinctive columns to claim
            hits = sum(1 for c in cols if c in _OPENMM_STATEDATA_COLS)
            return hits >= 3
        if ext == ".dcd":
            # DCD is a clear MD trajectory but doesn't uniquely mean OpenMM —
            # detect only if a sibling .pdb or StateDataReporter CSV exists
            return self._has_openmm_sibling(file_path)
        if ext in (".h5", ".hdf5"):
            # HDF5 trajectory written by MDTraj; signature check is hard without
            # h5py — claim only with sibling
            return self._has_openmm_sibling(file_path)
        if ext == ".pdb":
            head = self._read_head(file_path, max_bytes=4096)
            # OpenMM PDB output has a REMARK line at the top
            return "REMARK   1 CREATED WITH OPENMM" in head.upper()
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        ext = os.path.splitext(file_path)[1].lower()
        result = CompChemParsedResult(
            software_name="OpenMM",
            software_version=None,
            run_kind=RunKind.MOLECULAR_DYNAMICS,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        if ext == ".csv":
            self._parse_statedata(file_path, result)
        elif ext == ".pdb":
            self._parse_pdb(file_path, result)
        else:
            result.metadata["binary_artifact"] = True
            result.metadata["artifact_kind"] = "trajectory"
            result.termination_status = TerminationStatus.UNKNOWN
        return result

    def _parse_statedata(self, path: str, result: CompChemParsedResult) -> None:
        # Use Python's csv module on the whole file (StateDataReporter CSVs
        # are small even for long runs — one row per report interval)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                # Strip leading '#' some scripts add to the header
                first = f.readline()
                header_line = first.lstrip("#").strip()
                cols = [c.strip().lstrip('"').rstrip('"').lower() for c in header_line.split(",")]

                reader = csv.reader(f)
                rows = list(reader)
        except OSError:
            result.parse_warnings.append("Could not read StateDataReporter CSV")
            result.termination_status = TerminationStatus.UNKNOWN
            return

        if not rows:
            result.parse_warnings.append("StateDataReporter CSV has no rows")
            result.termination_status = TerminationStatus.PARTIAL
            return

        # Index lookup
        def col_idx(name: str):
            try:
                return cols.index(name)
            except ValueError:
                return None

        step_i = col_idx("step")
        time_i = col_idx("time (ps)")
        pe_i = col_idx("potential energy (kj/mole)")
        ke_i = col_idx("kinetic energy (kj/mole)")
        te_i = col_idx("total energy (kj/mole)")
        temp_i = col_idx("temperature (k)")

        n_frames = len(rows)
        result.metadata["n_frames"] = n_frames

        # Final values come from the last row
        last = rows[-1]
        if time_i is not None:
            try:
                result.metadata["total_time_ps"] = float(last[time_i])
                result.metrics.append(CompChemMetric(
                    name="simulated_time",
                    value=float(last[time_i]),
                    unit="ps",
                ))
            except (ValueError, IndexError):
                pass

        if pe_i is not None:
            try:
                pe_vals = [float(r[pe_i]) for r in rows if len(r) > pe_i]
                if pe_vals:
                    result.metrics.append(CompChemMetric(
                        name="mean_potential_energy",
                        value=sum(pe_vals) / len(pe_vals),
                        unit="kJ/mol",
                    ))
                    result.metadata["pe_range_kJ_per_mol"] = [min(pe_vals), max(pe_vals)]
            except (ValueError, IndexError):
                pass

        if temp_i is not None:
            try:
                temps = [float(r[temp_i]) for r in rows if len(r) > temp_i]
                if temps:
                    result.metrics.append(CompChemMetric(
                        name="mean_temperature",
                        value=sum(temps) / len(temps),
                        unit="K",
                    ))
            except (ValueError, IndexError):
                pass

        # If we got this far with a non-empty file, treat as normal termination.
        # The user's script controls how the file ends, so we don't have a
        # strict "end of run" marker — but parseable data plus a final time
        # value is the best signal we have.
        if "total_time_ps" in result.metadata:
            result.termination_status = TerminationStatus.NORMAL
        else:
            result.termination_status = TerminationStatus.PARTIAL

    def _parse_pdb(self, path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(path, max_bytes=4096)
        # OpenMM stamps version in a REMARK line on output PDBs
        for line in head.splitlines():
            up = line.upper()
            if "OPENMM" in up and "VERSION" in up:
                parts = line.split()
                for tok in parts:
                    if tok.replace(".", "").isdigit():
                        result.software_version = tok
                        break
        result.metadata["artifact_kind"] = "coordinate_snapshot"
        # Count ATOM/HETATM lines (cheap)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                n_atoms = sum(
                    1 for line in f if line.startswith(("ATOM  ", "HETATM"))
                )
            result.metadata["n_atoms"] = n_atoms
        except OSError:
            pass
        result.termination_status = TerminationStatus.NORMAL

    @staticmethod
    def _has_openmm_sibling(path: str) -> bool:
        d = os.path.dirname(path) or "."
        try:
            entries = os.listdir(d)
        except OSError:
            return False
        # Any sibling .pdb stamped with OpenMM, or a StateDataReporter CSV
        for e in entries:
            full = os.path.join(d, e)
            el = e.lower()
            if el.endswith(".pdb"):
                head = CompChemParser._read_head(full, max_bytes=2048)
                if "OPENMM" in head.upper():
                    return True
            if el.endswith(".csv"):
                head = CompChemParser._read_head(full, max_bytes=512)
                first = head.split("\n", 1)[0].lower()
                hits = sum(1 for c in first.split(",") if c.strip().strip('"') in _OPENMM_STATEDATA_COLS)
                if hits >= 3:
                    return True
        return False
