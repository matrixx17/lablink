from __future__ import annotations

from pathlib import Path

from moltrack_parsers.models import FileType, MetricValue, base_result


def detect(filepath: str, head: bytes) -> bool:
    return Path(filepath).suffix.lower() == ".sdf" or b"$$$$" in head


def parse(filepath: str):
    result = base_result(filepath, FileType.SDF, "RDKit")
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Descriptors, rdMolDescriptors  # type: ignore

        mols = [m for m in Chem.SDMolSupplier(filepath, sanitize=True) if m is not None]
        result.raw_metadata["molecule_count"] = len(mols)
        if mols:
            mol = mols[0]
            result.raw_metadata["canonical_smiles"] = Chem.MolToSmiles(mol, canonical=True)
            inchi = Chem.MolToInchi(mol)
            result.raw_metadata["inchi"] = inchi
            result.raw_metadata["inchi_key"] = Chem.InchiToInchiKey(inchi)
            result.extracted_metrics["molwt"] = MetricValue(float(Descriptors.MolWt(mol)), "g/mol")
            result.extracted_metrics["logp"] = MetricValue(float(Descriptors.MolLogP(mol)), "log_units")
            result.extracted_metrics["tpsa"] = MetricValue(float(Descriptors.TPSA(mol)), "Å²")
            result.extracted_metrics["hbd"] = MetricValue(float(Descriptors.NumHDonors(mol)), "count")
            result.extracted_metrics["hba"] = MetricValue(float(Descriptors.NumHAcceptors(mol)), "count")
            result.extracted_metrics["rotb"] = MetricValue(float(Descriptors.NumRotatableBonds(mol)), "count")
            result.raw_metadata["formula"] = rdMolDescriptors.CalcMolFormula(mol)
    except Exception as e:
        result.raw_metadata["rdkit_error"] = str(e)
    return result
