"""
RDKit-generated descriptor table parser.

Pattern: pandas.to_csv() of a DataFrame where each row is a molecule and
columns include SMILES + a set of RDKit descriptors (MolWt, LogP, TPSA,
NumHDonors, NumHAcceptors, NumRotatableBonds, etc.).

We don't actually call RDKit in the parser — the parsing here is just
schema recognition + descriptor extraction. The agent then forwards the
table to the API which will canonicalise the SMILES properly (Layer 2).
"""

import csv
import os
from typing import List, Optional

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)


# Descriptor column names commonly produced by RDKit's Descriptors module
# (lowercased here for case-insensitive matching).
_RDKIT_DESCRIPTOR_COLS = {
    "molwt", "mol_wt", "molecular_weight", "mw",
    "logp", "mol_logp", "molmrlogp", "crippen_logp", "alogp",
    "tpsa", "mol_tpsa",
    "numhdonors", "num_hdonors", "hbd",
    "numhacceptors", "num_hacceptors", "hba",
    "numrotatablebonds", "num_rotatable_bonds", "rotb",
    "numaromaticrings", "num_aromatic_rings",
    "fractioncsp3", "fraction_csp3",
    "qed",
    "heavyatomcount", "heavy_atom_count", "num_heavy_atoms",
}

_SMILES_COL_NAMES = {"smiles", "canonical_smiles", "smi", "structure"}


class RDKitTableParser(CompChemParser):
    name = "rdkit_property_table"
    software_name = "RDKit"
    run_kinds = [RunKind.PROPERTY_PREDICTION]

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".sdf"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".sdf":
            head = self._read_head(file_path, max_bytes=8192)
            # Look for SDF tags that name RDKit descriptors
            return any(
                f"> <{col}>" in head.lower().replace(">  <", "> <")
                for col in _RDKIT_DESCRIPTOR_COLS
            )
        if ext in (".csv", ".tsv"):
            head = self._read_head(file_path, max_bytes=2048)
            first_line = head.split("\n", 1)[0]
            delim = "\t" if ext == ".tsv" else ","
            cols = [c.strip().lower().strip('"') for c in first_line.split(delim)]
            has_smiles = any(c in _SMILES_COL_NAMES for c in cols)
            descriptor_hits = sum(1 for c in cols if c in _RDKIT_DESCRIPTOR_COLS)
            # Need a SMILES column AND at least 2 descriptor columns to claim
            return has_smiles and descriptor_hits >= 2
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        ext = os.path.splitext(file_path)[1].lower()
        result = CompChemParsedResult(
            software_name="RDKit",
            software_version=None,
            run_kind=RunKind.PROPERTY_PREDICTION,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        if ext == ".sdf":
            self._parse_sdf(file_path, result)
        else:
            self._parse_csv(file_path, result, delim="\t" if ext == ".tsv" else ",")
        return result

    def _parse_csv(self, path: str, result: CompChemParsedResult, delim: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f, delimiter=delim)
                rows = list(reader)
        except OSError:
            result.parse_warnings.append("Could not read property table")
            result.termination_status = TerminationStatus.UNKNOWN
            return

        if len(rows) < 2:
            result.parse_warnings.append("Property table has no data rows")
            result.termination_status = TerminationStatus.PARTIAL
            return

        header = [c.strip().lower() for c in rows[0]]
        data_rows = rows[1:]
        n_molecules = len(data_rows)

        result.metadata["n_molecules"] = n_molecules
        result.metadata["descriptor_columns"] = [
            h for h in header if h in _RDKIT_DESCRIPTOR_COLS
        ]

        # Emit table-level summary metrics. Per-molecule properties are
        # forwarded to the API as a separate manifest payload (the agent
        # will package them).
        result.metadata["smiles_column"] = next(
            (h for h in header if h in _SMILES_COL_NAMES), None
        )

        # Aggregate descriptors into mean/range metrics for the table
        for col in result.metadata["descriptor_columns"]:
            try:
                idx = header.index(col)
                vals: List[float] = []
                for row in data_rows:
                    if idx < len(row):
                        try:
                            vals.append(float(row[idx]))
                        except ValueError:
                            continue
                if vals:
                    result.metrics.append(CompChemMetric(
                        name=f"mean_{col}",
                        value=sum(vals) / len(vals),
                        unit=self._guess_unit(col),
                        metadata={"n_molecules": len(vals)},
                    ))
            except ValueError:
                continue

        result.termination_status = TerminationStatus.NORMAL

    def _parse_sdf(self, path: str, result: CompChemParsedResult) -> None:
        # Light SDF scan: count molecules ($$$$ delimiters), tag names.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            result.parse_warnings.append("Could not read SDF property file")
            result.termination_status = TerminationStatus.UNKNOWN
            return

        n_molecules = text.count("$$$$")
        result.metadata["n_molecules"] = n_molecules

        # Discover tags
        tags = set()
        for line in text.splitlines():
            if line.startswith("> <") and line.endswith(">"):
                tags.add(line[3:-1])
        result.metadata["sdf_tags"] = sorted(tags)

        descriptor_tags = [t for t in tags if t.lower() in _RDKIT_DESCRIPTOR_COLS]
        result.metadata["descriptor_columns"] = descriptor_tags

        result.termination_status = TerminationStatus.NORMAL if n_molecules > 0 \
            else TerminationStatus.PARTIAL

    @staticmethod
    def _guess_unit(col: str) -> str:
        c = col.lower()
        if c in {"molwt", "mol_wt", "molecular_weight", "mw"}:
            return "g/mol"
        if c in {"tpsa", "mol_tpsa"}:
            return "Å²"
        if c in {"logp", "mol_logp", "molmrlogp", "crippen_logp", "alogp"}:
            return "log_units"
        if c == "qed":
            return "dimensionless"
        # Counts (NumHDonors, NumRotatableBonds, etc.)
        return "count"
