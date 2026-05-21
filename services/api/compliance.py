"""
SOC 2 readiness checklist and security posture documentation (non-certifying).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List


def soc2_readiness_checklist() -> Dict[str, Any]:
    """Static readiness tracker — update as controls are implemented."""
    controls: List[Dict[str, Any]] = [
        {
            "control": "CC6.1 Logical access",
            "status": "in_progress",
            "notes": "API key auth via X-API-Key; set AUTH_REQUIRED=true in production.",
        },
        {
            "control": "CC6.6 Encryption in transit",
            "status": "partial",
            "notes": "TLS terminates at reverse proxy; enable S3_SECURE for AWS.",
        },
        {
            "control": "CC7.2 System monitoring",
            "status": "implemented",
            "notes": "Structured JSON logging, health endpoints, circuit breakers.",
        },
        {
            "control": "CC8.1 Change management",
            "status": "partial",
            "notes": "Alembic migrations; CI/CD pipeline not yet configured.",
        },
        {
            "control": "A1.2 Availability",
            "status": "partial",
            "notes": "Docker healthchecks; multi-AZ deployment documented in prod compose.",
        },
        {
            "control": "PI1 Processing integrity",
            "status": "in_progress",
            "notes": "21 CFR Part 11 audit hash chain; bioprocess QC rules.",
        },
        {
            "control": "C1 Confidentiality",
            "status": "in_progress",
            "notes": "Org-scoped data isolation; CDMO sponsor separation via org_id.",
        },
    ]

    implemented = sum(1 for c in controls if c["status"] == "implemented")
    in_progress = sum(1 for c in controls if c["status"] == "in_progress")
    partial = sum(1 for c in controls if c["status"] == "partial")

    return {
        "framework": "SOC 2 Type II readiness (self-assessment)",
        "disclaimer": "This is not a SOC 2 report or certification.",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "implemented": implemented,
            "in_progress": in_progress,
            "partial": partial,
            "total": len(controls),
        },
        "controls": controls,
        "production_checklist": [
            "Set AUTH_REQUIRED=true",
            "Rotate LABLINK_BOOTSTRAP_API_KEY and issue per-org API keys",
            "Enable HTTPS and S3_SECURE",
            "Complete CSV validation package for edge agent",
            "Enable JSON_LOGS and ship to SIEM",
        ],
    }
