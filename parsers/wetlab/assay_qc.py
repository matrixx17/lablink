"""
Assay-specific QC checks for wet lab CSV tables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .assay_format import identify_plate_positions, normalize_header


@dataclass
class QCResult:
    """Single assay QC check result."""

    rule: str
    status: str
    message: str
    value: Optional[float] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["severity"] = self.status
        return out


_SEVERITY_RANK = {"pass": 0, "warn": 1, "fail": 2}


def _compact(header: object) -> str:
    return "".join(ch for ch in normalize_header(header) if ch.isalnum() or ch == "%")


def _matches(header: object, patterns: Iterable[str]) -> bool:
    normalized = normalize_header(header)
    compacted = _compact(header)
    for pattern in patterns:
        p_norm = normalize_header(pattern)
        p_compact = _compact(pattern)
        if p_norm and p_norm in normalized:
            return True
        if p_compact and p_compact in compacted:
            return True
    return False


def _find_column(headers: Sequence[object], patterns: Iterable[str]) -> Optional[str]:
    for header in headers:
        if _matches(header, patterns):
            return str(header)
    return None


def _find_columns(headers: Sequence[object], patterns: Iterable[str]) -> List[str]:
    return [str(header) for header in headers if _matches(header, patterns)]


def _numeric_series(df: pd.DataFrame, column: Optional[str]) -> pd.Series:
    if not column or column not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[column], errors="coerce").dropna()


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(out) or np.isinf(out):
        return None
    return out


def _finding(
    rule: str,
    severity: str,
    message: str,
    value: Optional[float] = None,
    **details: Any,
) -> QCResult:
    return QCResult(
        rule=rule,
        status=severity,
        message=message,
        value=value,
        details=details or None,
    )


def _worst_status(results: Sequence[QCResult]) -> str:
    status = "pass"
    for result in results:
        if _SEVERITY_RANK.get(result.status, 0) > _SEVERITY_RANK.get(status, 0):
            status = result.status
    return status


def _unit_multiplier_to_um(column: str) -> float:
    normalized = normalize_header(column)
    compacted = _compact(column)
    if "mm" in compacted or "[mm]" in normalized:
        return 1000.0
    if "nm" in compacted or "[nm]" in normalized:
        return 0.001
    if "um" in compacted or "[um]" in normalized or "uM" in column:
        return 1.0
    if "pm" in compacted or "[pm]" in normalized:
        return 0.000001
    # Check molar last so "mM"/"uM"/"nM" do not get interpreted as M.
    if normalized.endswith(" m") or "[m]" in normalized or compacted.endswith("m"):
        return 1_000_000.0
    return 1.0


def _potency_unit_context(column: str, values: pd.Series) -> Tuple[float, str, bool]:
    normalized = normalize_header(column)
    compacted = _compact(column)

    if "nm" in compacted or "[nm]" in normalized:
        return 0.001, "nM", False
    if "um" in compacted or "[um]" in normalized or "uM" in column:
        return 1.0, "uM", False
    if "mm" in compacted or "[mm]" in normalized:
        return 1000.0, "mM", False
    if "pm" in compacted or "[pm]" in normalized:
        return 0.000001, "pM", False
    # Check molar last so "mM"/"uM"/"nM"/"pM" do not get interpreted as M.
    if normalized.endswith(" m") or "[m]" in normalized or compacted.endswith("m"):
        return 1_000_000.0, "M", False

    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if not numeric.empty:
        median = float(numeric.median())
        if median > 1000:
            return 0.001, "nM", True
        if median < 0.001:
            return 1_000_000.0, "M", True
    return 1.0, "uM", False


def _unit_label(column: Optional[str]) -> str:
    if not column:
        return "uM"
    normalized = normalize_header(column)
    compacted = _compact(column)
    if "nm" in compacted or "[nm]" in normalized:
        return "nM"
    if "um" in compacted or "[um]" in normalized or "uM" in column:
        return "uM"
    if "mm" in compacted or "[mm]" in normalized:
        return "mM"
    if "pm" in compacted or "[pm]" in normalized:
        return "pM"
    if normalized.endswith(" m") or "[m]" in normalized or compacted.endswith("m"):
        return "M"
    return "uM"


def _volume_multiplier_to_ul(column: str) -> float:
    normalized = normalize_header(column)
    compacted = _compact(column)
    if "ml" in compacted or "[ml]" in normalized:
        return 1000.0
    if "nl" in compacted or "[nl]" in normalized:
        return 0.001
    return 1.0


def _compound_column(headers: Sequence[object]) -> Optional[str]:
    return _find_column(headers, ["compound_id", "compound", "cmpd", "cpd", "name"])


def _concentration_column(headers: Sequence[object]) -> Optional[str]:
    return _find_column(headers, ["concentration", "conc", "dose", "[um]", "[nm]"])


def _response_column(headers: Sequence[object]) -> Optional[str]:
    return _find_column(headers, ["response", "inhibition", "activity", "signal", "%inh", "viability"])


def _potency_columns(headers: Sequence[object]) -> List[str]:
    return _find_columns(headers, ["ic50", "ec50", "ki", "kd"])


def _purity_column(headers: Sequence[object]) -> Optional[str]:
    return _find_column(headers, ["purity", "%purity", "area%"])


def _rt_column(headers: Sequence[object]) -> Optional[str]:
    return _find_column(headers, ["retention_time", "ret_time", "rt"])


def _volume_columns(headers: Sequence[object]) -> Tuple[Optional[str], Optional[str]]:
    v1 = _find_column(headers, ["v1", "stock_volume", "stock_vol"])
    v2 = _find_column(headers, ["v2", "final_volume", "working_volume", "total_volume"])
    generic = _find_columns(headers, ["volume", "vol"])
    for column in generic:
        if column != v1 and column != v2:
            if v1 is None:
                v1 = column
            elif v2 is None:
                v2 = column
                break
    return v1, v2


def _iter_compound_groups(df: pd.DataFrame, compound_col: Optional[str]):
    if compound_col and compound_col in df.columns:
        for compound, group in df.groupby(compound_col, dropna=False):
            yield str(compound), group
    else:
        yield "assay", df


def check_dose_min_points(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    concentration_col = _concentration_column(headers)
    if not concentration_col:
        return []

    results: List[QCResult] = []
    for compound, group in _iter_compound_groups(df, compound_col):
        n = int(_numeric_series(group, concentration_col).nunique())
        if n < 4:
            results.append(_finding(
                "dose_min_points",
                "fail",
                f"Insufficient dose points for curve fitting: {n}",
                n,
                compound=compound,
            ))
        elif n < 6:
            results.append(_finding(
                "dose_min_points",
                "warn",
                f"Insufficient dose points for curve fitting: {n}",
                n,
                compound=compound,
            ))
    return results


def check_dose_monotonicity(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    concentration_col = _concentration_column(headers)
    response_col = _response_column(headers)
    if not concentration_col or not response_col:
        return []

    results: List[QCResult] = []
    for compound, group in _iter_compound_groups(df, compound_col):
        pair = group[[concentration_col, response_col]].copy()
        pair[concentration_col] = pd.to_numeric(pair[concentration_col], errors="coerce")
        pair[response_col] = pd.to_numeric(pair[response_col], errors="coerce")
        pair = pair.dropna().sort_values(concentration_col)
        if len(pair) < 3:
            continue

        responses = pair[response_col].astype(float).to_numpy()
        trend = 1.0 if responses[-1] >= responses[0] else -1.0
        deltas = np.diff(responses) * trend
        inversions = int(np.sum(deltas < 0))

        if inversions > 4:
            severity = "fail"
        elif inversions > 2:
            severity = "warn"
        else:
            continue
        results.append(_finding(
            "dose_monotonicity",
            severity,
            f"Non-monotonic dose-response: {inversions} inversions",
            inversions,
            compound=compound,
        ))
    return results


def check_dose_plateau(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    response_col = _response_column(headers)
    if not response_col:
        return []

    responses = _numeric_series(df, response_col)
    if responses.empty:
        return []

    assay_max = float(responses.max())
    if assay_max <= 0:
        return []

    results: List[QCResult] = []
    for compound, group in _iter_compound_groups(df, compound_col):
        group_responses = _numeric_series(group, response_col)
        if group_responses.empty:
            continue
        top = float(group_responses.max())
        bottom = float(group_responses.min())
        if top < assay_max * 0.70:
            results.append(_finding(
                "dose_plateau_top",
                "warn",
                "Top plateau below 70% of assay maximum; curve may not have reached saturation.",
                top,
                compound=compound,
                assay_max=assay_max,
            ))
        if bottom > assay_max * 0.30:
            results.append(_finding(
                "dose_plateau_bottom",
                "warn",
                "Bottom plateau above 30% of assay maximum; poor baseline.",
                bottom,
                compound=compound,
                assay_max=assay_max,
            ))
    return results


def check_replicate_consistency(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    concentration_col = _concentration_column(headers)
    response_col = _response_column(headers)
    if not concentration_col or not response_col:
        return []

    unit = _unit_label(concentration_col)
    results: List[QCResult] = []
    for compound, compound_group in _iter_compound_groups(df, compound_col):
        pair = compound_group[[concentration_col, response_col]].copy()
        pair[concentration_col] = pd.to_numeric(pair[concentration_col], errors="coerce")
        pair[response_col] = pd.to_numeric(pair[response_col], errors="coerce")
        pair = pair.dropna()
        if pair.empty:
            continue

        for conc, group in pair.groupby(concentration_col):
            responses = group[response_col].astype(float)
            if len(responses) < 2:
                continue
            mean = float(responses.mean())
            if mean == 0:
                continue
            cv = float(responses.std(ddof=1) / abs(mean) * 100.0)
            if cv > 50.0:
                severity = "fail"
            elif cv > 20.0:
                severity = "warn"
            else:
                continue
            results.append(_finding(
                "replicate_consistency",
                severity,
                f"High replicate variability at {conc:g} {unit}: CV = {cv:.1f}%",
                cv,
                compound=compound,
                concentration=float(conc),
                concentration_unit=unit,
                response_column=response_col,
                replicate_count=int(len(responses)),
            ))
    return results


def check_dmso_dilution(df: pd.DataFrame, tolerance: float = 0.05) -> List[QCResult]:
    headers = list(df.columns)
    stock_col = _find_column(headers, ["stock_conc", "stock_concentration", "c1", "[mm] stock"])
    working_col = _find_column(headers, ["final_conc", "working_conc", "c2", "[um] final"])
    v1_col, v2_col = _volume_columns(headers)
    if not stock_col or not working_col or not v1_col or not v2_col:
        return []

    stock_multiplier = _unit_multiplier_to_um(stock_col)
    working_multiplier = _unit_multiplier_to_um(working_col)
    v1_multiplier = _volume_multiplier_to_ul(v1_col)
    v2_multiplier = _volume_multiplier_to_ul(v2_col)
    results: List[QCResult] = []
    for idx, row in df.iterrows():
        stock = _safe_float(row.get(stock_col))
        actual = _safe_float(row.get(working_col))
        v1 = _safe_float(row.get(v1_col))
        v2 = _safe_float(row.get(v2_col))
        if stock is None or actual is None or v1 is None or v2 in (None, 0):
            continue

        expected_um = stock * stock_multiplier * (v1 * v1_multiplier) / (v2 * v2_multiplier)
        actual_um = actual * working_multiplier
        allowed = abs(expected_um) * tolerance
        if abs(actual_um - expected_um) > allowed:
            results.append(_finding(
                "dmso_dilution",
                "fail",
                (
                    f"DMSO dilution error row {int(idx) + 1}: "
                    f"expected {expected_um:.3g} uM, got {actual_um:.3g} uM "
                    "(C1V1≠C2V2)"
                ),
                actual_um,
                row=int(idx) + 1,
                expected_um=expected_um,
                actual_um=actual_um,
                stock_column=stock_col,
                working_column=working_col,
                v1_column=v1_col,
                v2_column=v2_col,
            ))
    return results


def check_potency_range(df: pd.DataFrame) -> List[QCResult]:
    results: List[QCResult] = []
    lower_um = 0.000001  # 1 pM
    upper_um = 100.0
    for column in _potency_columns(list(df.columns)):
        values = _numeric_series(df, column)
        multiplier, source_unit, unit_inferred = _potency_unit_context(column, values)
        for value in values:
            value_um = float(value) * multiplier
            if value_um < lower_um or value_um > upper_um:
                details = {
                    "column": column,
                    "value_um": value_um,
                    "source_unit": source_unit,
                }
                if unit_inferred:
                    details["unit_inferred"] = True
                results.append(_finding(
                    "potency_range",
                    "warn",
                    f"Potency value {value} outside expected range 1pM-100uM — verify units",
                    float(value),
                    **details,
                ))
    return results


def check_duplicate_compounds(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    potency_cols = _potency_columns(headers)
    if not compound_col or not potency_cols:
        return []

    results: List[QCResult] = []
    for compound, group in df.groupby(compound_col, dropna=False):
        compound_id = str(compound)
        for column in potency_cols:
            values = _numeric_series(group, column).astype(float).tolist()
            if len(values) < 2:
                continue
            positive = [v for v in values if v > 0]
            if len(positive) < 2:
                continue
            low = min(positive)
            high = max(positive)
            fold = high / low if low else float("inf")
            if fold > 2.0:
                results.append(_finding(
                    "potency_duplicate",
                    "warn",
                    f"Duplicate compound {compound_id}: values {low:g} and {high:g} differ {fold:.2g}x",
                    fold,
                    compound=compound_id,
                    column=column,
                    values=values,
                ))
    return results


def check_hplc_purity(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    purity_col = _purity_column(headers)
    compound_col = _compound_column(headers)
    if not purity_col:
        return []

    results: List[QCResult] = []
    for idx, row in df.iterrows():
        value = _safe_float(row.get(purity_col))
        if value is None:
            continue
        compound = str(row.get(compound_col)) if compound_col else f"row {int(idx) + 1}"
        if value < 85:
            severity = "fail"
        elif value < 95:
            severity = "warn"
        else:
            continue
        results.append(_finding(
            "hplc_purity_threshold",
            severity,
            f"Low purity: {compound} at {value:g}% (threshold 95%)",
            value,
            compound=compound,
            row=int(idx) + 1,
        ))
    return results


def check_hplc_rt_variability(df: pd.DataFrame) -> List[QCResult]:
    headers = list(df.columns)
    compound_col = _compound_column(headers)
    rt_col = _rt_column(headers)
    if not compound_col or not rt_col:
        return []

    results: List[QCResult] = []
    for compound, group in df.groupby(compound_col, dropna=False):
        values = _numeric_series(group, rt_col)
        if len(values) < 2:
            continue
        min_rt = float(values.min())
        max_rt = float(values.max())
        if max_rt - min_rt > 0.5:
            results.append(_finding(
                "hplc_rt_variability",
                "warn",
                f"RT variability for {compound}: {min_rt:g}-{max_rt:g} min",
                max_rt - min_rt,
                compound=str(compound),
                min_rt=min_rt,
                max_rt=max_rt,
            ))
    return results


def check_plate_control_wells(df: pd.DataFrame) -> List[QCResult]:
    if df is None or df.empty or len(df) != 96:
        return []

    response_col = _response_column(list(df.columns))
    if not response_col:
        return []

    positions = identify_plate_positions(df)
    if not positions:
        return []

    by_index = {position["index"]: position for position in positions}
    corners = {(1, 1), (1, 12), (8, 1), (8, 12)}
    non_zero = []
    for idx, row in df.iterrows():
        position = by_index.get(idx)
        if not position or (position["row"], position["column"]) not in corners:
            continue
        value = _safe_float(row.get(response_col))
        if value is not None and value != 0:
            non_zero.append({
                "well": position["well"],
                "response": value,
            })

    if not non_zero:
        return []
    return [_finding(
        "plate_control_wells",
        "warn",
        "Plate data detected (96 wells). Verify control well assignments.",
        float(len(non_zero)),
        plate_format=96,
        wells=non_zero,
        response_column=response_col,
    )]


class AssayQCEngine:
    """Run assay-specific QC checks for generic wet lab assay tables."""

    def run(self, df: pd.DataFrame, assay_format: str) -> List[QCResult]:
        assay_format = (assay_format or "unknown").lower()
        if df is None or df.empty:
            return []

        if assay_format == "dose_response":
            return (
                check_dose_min_points(df)
                + check_dose_monotonicity(df)
                + check_dose_plateau(df)
                + check_dmso_dilution(df)
                + check_replicate_consistency(df)
                + check_plate_control_wells(df)
            )
        if assay_format == "potency_summary":
            return check_potency_range(df) + check_duplicate_compounds(df) + check_plate_control_wells(df)
        if assay_format == "hplc_purity":
            return check_hplc_purity(df) + check_hplc_rt_variability(df) + check_plate_control_wells(df)
        return []

    def summary(
        self,
        df: pd.DataFrame,
        assay_format: str,
        stats: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        results = self.run(df, assay_format)
        overall = _worst_status(results)
        return {
            "qc_mode": "assay",
            "assay_format": assay_format,
            "qc_flags": {},
            "domain_findings": [result.to_dict() for result in results],
            "overall_status": overall,
            "summary": _summary_for_results(results, assay_format),
        }


def _summary_for_results(results: Sequence[QCResult], assay_format: str) -> str:
    if not results:
        return f"All {assay_format} assay QC checks passed."
    messages = "; ".join(result.message for result in results[:3])
    return f"Assay QC: {messages}"
