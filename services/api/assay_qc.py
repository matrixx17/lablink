"""
Wet lab assay QC summary adapter for the API ingest path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from parsers.wetlab.assay_qc import AssayQCEngine
from qc import QCStatus, qc_summary


def assay_qc_summary(
    stats: Dict[str, Any],
    assay_format: str,
    precomputed_findings: Optional[List[Dict[str, Any]]] = None,
    historical_baselines: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Run generic QC and layer parser-time assay findings into the same shape
    used by the existing domain QC summaries.
    """
    base = qc_summary(stats=stats, historical_baselines=historical_baselines)
    domain_findings = [
        _normalize_finding(finding)
        for finding in (precomputed_findings or [])
        if isinstance(finding, dict)
    ]

    base["domain_findings"] = domain_findings
    base["qc_mode"] = "assay"
    base["assay_format"] = assay_format

    if domain_findings:
        severities = [finding.get("severity") for finding in domain_findings]
        if "fail" in severities:
            base["overall_status"] = QCStatus.FAIL.value
        elif base.get("overall_status") == QCStatus.PASS.value and "warn" in severities:
            base["overall_status"] = QCStatus.WARN.value

        domain_msgs = "; ".join(finding["message"] for finding in domain_findings[:3])
        base["summary"] = f"{base['summary']} Assay: {domain_msgs}"

    return base


def _normalize_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(finding)
    severity = out.get("severity") or out.get("status") or "warn"
    out["severity"] = severity
    out.setdefault("status", severity)
    out.setdefault("rule", "assay_qc")
    out.setdefault("message", "")
    return out


__all__ = ["AssayQCEngine", "assay_qc_summary"]
