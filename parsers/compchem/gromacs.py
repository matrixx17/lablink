"""
GROMACS file parsers.

GROMACS produces a constellation of files per run:
  .tpr   binary run input (topology + parameters + initial coords)
  .gro   text coordinate snapshot
  .xtc   binary compressed trajectory (frequent frames, lossy)
  .trr   binary full-precision trajectory (less frequent)
  .edr   binary energy file
  .log   text log with step info, performance, termination

The .log file is the single most informative ingest target — it carries
software version, command line, n_atoms, simulation time, and termination
status. We parse .log fully and treat the binary files as upload-only
artifacts (the API stores them as RunInput/RunOutput records with hashes
but no extracted metrics).
"""

import os
import re
from datetime import datetime
from typing import List, Optional

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)


_GROMACS_HEADER_RE = re.compile(r"GROMACS\s+version", re.IGNORECASE)
_GROMACS_LOG_RE = re.compile(r"^\s*Log file opened on|GROMACS:\s+gmx", re.MULTILINE)
_GROMACS_VERSION_RE = re.compile(r"GROMACS version:\s+(\S+)", re.IGNORECASE)
_GROMACS_VERSION_ALT_RE = re.compile(r":-\)\s+GROMACS\s+-\s+\S+\s+(\S+)\s+\(-:", re.IGNORECASE)
_GROMACS_CMDLINE_RE = re.compile(r"Command line:\s*\n\s*(.+)", re.IGNORECASE)
_GROMACS_NATOMS_RE = re.compile(r"Number of atoms:\s+(\d+)", re.IGNORECASE)
_GROMACS_TIMESTEP_RE = re.compile(r"\s+dt\s*=\s*([\d.]+)")
_GROMACS_NSTEPS_RE = re.compile(r"\s+nsteps\s*=\s*(\d+)")
_GROMACS_NORMAL_RE = re.compile(
    r"Finished mdrun|GROMACS reminds you|Performance:|Writing checkpoint, step",
    re.IGNORECASE,
)
_GROMACS_FATAL_RE = re.compile(r"Fatal error|Halting program", re.IGNORECASE)
_GROMACS_FF_RE = re.compile(r"Forcefield was read from:\s*(.+)|forcefield\s*=\s*(\S+)", re.IGNORECASE)
_GROMACS_WALL_TIME_RE = re.compile(r"Time:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)")


class GROMACSParser(CompChemParser):
    name = "gromacs"
    software_name = "GROMACS"
    run_kinds = [RunKind.MOLECULAR_DYNAMICS, RunKind.FREE_ENERGY]

    # We declare .log + .gro for detection; binaries handled separately
    def supported_extensions(self) -> List[str]:
        return [".log", ".gro", ".tpr", ".xtc", ".trr", ".edr"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".tpr", ".xtc", ".trr", ".edr"):
            # Binary GROMACS files — magic bytes detection is fragile; rely on
            # extension + sibling presence
            return self._has_gromacs_sibling(file_path)
        if ext == ".log":
            head = self._read_head(file_path, max_bytes=8192)
            return bool(
                _GROMACS_HEADER_RE.search(head)
                or _GROMACS_LOG_RE.search(head)
            )
        if ext == ".gro":
            # .gro is a 2-line-header text file. Very loose check.
            head = self._read_head(file_path, max_bytes=512)
            lines = head.split("\n")
            if len(lines) < 3:
                return False
            # Line 2 should be an integer (n_atoms)
            try:
                int(lines[1].strip())
                return self._has_gromacs_sibling(file_path)
            except ValueError:
                return False
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        ext = os.path.splitext(file_path)[1].lower()
        result = CompChemParsedResult(
            software_name="GROMACS",
            software_version=None,
            run_kind=RunKind.MOLECULAR_DYNAMICS,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        if ext == ".log":
            self._parse_log(file_path, result)
        elif ext == ".gro":
            self._parse_gro(file_path, result)
        else:
            # Binary — minimal metadata; the agent uploads as-is.
            result.metadata["binary_artifact"] = True
            result.metadata["artifact_kind"] = {
                ".tpr": "run_input",
                ".xtc": "trajectory",
                ".trr": "trajectory",
                ".edr": "energy",
            }.get(ext, "binary")
            result.termination_status = TerminationStatus.UNKNOWN
            sibling_log = self._find_sibling_log(file_path)
            if sibling_log:
                # Reuse metadata from the log for this binary so it inherits
                # software version, termination status, etc.
                self._parse_log(sibling_log, result)
                result.parse_warnings.append(
                    f"Metadata inherited from {os.path.basename(sibling_log)}"
                )
        return result

    def _parse_log(self, path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(path, max_bytes=32768)
        tail = self._read_tail(path, max_bytes=32768)
        full = head + "\n" + tail

        m = _GROMACS_VERSION_RE.search(head) or _GROMACS_VERSION_ALT_RE.search(head)
        if m:
            result.software_version = m.group(1)

        m = _GROMACS_CMDLINE_RE.search(head)
        if m:
            result.cli_args = m.group(1).strip()

        m = _GROMACS_NATOMS_RE.search(head)
        if m:
            result.metadata["n_atoms"] = int(m.group(1))

        m = _GROMACS_TIMESTEP_RE.search(head)
        if m:
            try:
                dt_ps = float(m.group(1))
                result.metadata["timestep_ps"] = dt_ps
            except ValueError:
                pass

        m = _GROMACS_NSTEPS_RE.search(head)
        if m:
            try:
                nsteps = int(m.group(1))
                result.metadata["n_steps"] = nsteps
                if "timestep_ps" in result.metadata:
                    result.metadata["total_time_ps"] = nsteps * result.metadata["timestep_ps"]
            except ValueError:
                pass

        m = _GROMACS_FF_RE.search(full)
        if m:
            ff = (m.group(1) or m.group(2) or "").strip()
            if ff:
                result.forcefield = ff

        # Wall-clock time (last "Time:" line in the log)
        wall_matches = list(_GROMACS_WALL_TIME_RE.finditer(tail))
        if wall_matches:
            try:
                # GROMACS format: "Time:    NodeTime    NS/day    hour/ns"
                node_time_s = float(wall_matches[-1].group(1))
                result.wall_time_s = node_time_s
            except ValueError:
                pass

        if _GROMACS_FATAL_RE.search(full):
            result.termination_status = TerminationStatus.CRASHED
            fatal_line = next(
                (l.strip() for l in full.splitlines() if "Fatal error" in l),
                None,
            )
            result.error_message = fatal_line
        elif _GROMACS_NORMAL_RE.search(tail):
            result.termination_status = TerminationStatus.NORMAL
        else:
            result.termination_status = TerminationStatus.PARTIAL

        # MD doesn't have a single "score" but we expose total simulated
        # time as a metric for downstream queries
        if "total_time_ps" in result.metadata:
            result.metrics.append(CompChemMetric(
                name="simulated_time",
                value=result.metadata["total_time_ps"],
                unit="ps",
            ))

    def _parse_gro(self, path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(path, max_bytes=1024)
        lines = head.split("\n", 3)
        if len(lines) >= 2:
            try:
                result.metadata["n_atoms"] = int(lines[1].strip())
            except ValueError:
                pass
        result.metadata["artifact_kind"] = "coordinate_snapshot"
        result.termination_status = TerminationStatus.NORMAL

    @staticmethod
    def _has_gromacs_sibling(path: str) -> bool:
        """A binary GROMACS file is more confidently identified when a sibling
        .log or .tpr exists in the same directory."""
        d = os.path.dirname(path) or "."
        try:
            entries = os.listdir(d)
        except OSError:
            return False
        return any(
            e.lower().endswith((".log", ".tpr", ".mdp")) for e in entries
        )

    @staticmethod
    def _find_sibling_log(path: str) -> Optional[str]:
        d = os.path.dirname(path) or "."
        stem = os.path.splitext(os.path.basename(path))[0]
        candidate = os.path.join(d, f"{stem}.log")
        if os.path.isfile(candidate):
            return candidate
        # Otherwise: first .log in the same directory
        try:
            for e in os.listdir(d):
                if e.lower().endswith(".log"):
                    full = os.path.join(d, e)
                    head = CompChemParser._read_head(full, max_bytes=2048)
                    if _GROMACS_HEADER_RE.search(head) or _GROMACS_LOG_RE.search(head):
                        return full
        except OSError:
            pass
        return None
