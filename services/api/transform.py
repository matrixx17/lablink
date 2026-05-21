"""
Data transformation layer for LabLink AI.

Transforms parsed instrument data into standardized formats that
downstream systems (LIMS, ELN, dashboards) can consume.

Supported output formats:
- LabLink Standard Format (LSF): Our own simple JSON schema
- Allotrope Simple Model (ASM): Partial implementation for compatibility
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field, asdict

# Version of the transformation module
TRANSFORM_VERSION = "1.0.0"

# LabLink Standard Format version
LSF_VERSION = "1.0"


# -----------------------------------------------------------------------------
# Unit Standardization
# -----------------------------------------------------------------------------

# Mapping of common unit variations to standardized units
UNIT_NORMALIZATION = {
    # Time
    "min": "min",
    "mins": "min",
    "minutes": "min",
    "sec": "s",
    "secs": "s",
    "seconds": "s",
    "s": "s",
    "hr": "h",
    "hrs": "h",
    "hours": "h",
    "h": "h",
    # Concentration
    "ppm": "ppm",
    "ppb": "ppb",
    "mg/l": "mg/L",
    "mg/L": "mg/L",
    "ug/l": "µg/L",
    "ug/L": "µg/L",
    "µg/l": "µg/L",
    "µg/L": "µg/L",
    "ng/ml": "ng/mL",
    "ng/mL": "ng/mL",
    "ug/ml": "µg/mL",
    "ug/mL": "µg/mL",
    "µg/ml": "µg/mL",
    "µg/mL": "µg/mL",
    "mg/ml": "mg/mL",
    "mg/mL": "mg/mL",
    "g/l": "g/L",
    "g/L": "g/L",
    "mol/l": "mol/L",
    "mol/L": "mol/L",
    "M": "mol/L",
    "mM": "mmol/L",
    "uM": "µmol/L",
    "µM": "µmol/L",
    "nM": "nmol/L",
    # Volume
    "ul": "µL",
    "uL": "µL",
    "µl": "µL",
    "µL": "µL",
    "ml": "mL",
    "mL": "mL",
    "l": "L",
    "L": "L",
    # Mass
    "ng": "ng",
    "ug": "µg",
    "µg": "µg",
    "mg": "mg",
    "g": "g",
    "kg": "kg",
    # Absorbance / optical
    "AU": "AU",
    "mAU": "mAU",
    "OD": "OD",
    # Temperature
    "C": "°C",
    "°C": "°C",
    "celsius": "°C",
    "F": "°F",
    "°F": "°F",
    "K": "K",
    # Area (chromatography)
    "mAU*s": "mAU·s",
    "mAU*min": "mAU·min",
    "AU*s": "AU·s",
    "AU*min": "AU·min",
    # Percentage
    "%": "%",
    "percent": "%",
    "pct": "%",
    # Dimensionless
    "": "dimensionless",
    "count": "count",
    "counts": "count",
}

# Field-to-unit mapping for common canonical fields
CANONICAL_FIELD_UNITS = {
    "retention_time": "min",
    "peak_area": "mAU·s",
    "peak_height": "mAU",
    "concentration": "mg/L",
    "absorbance": "AU",
    "optical_density": "OD",
    "temperature": "°C",
    "injection_volume": "µL",
    "flow_rate": "mL/min",
    "pressure": "bar",
    "wavelength": "nm",
    "mass_to_charge": "m/z",
    "intensity": "count",
}


def normalize_unit(unit: Optional[str], field_name: Optional[str] = None) -> str:
    """
    Normalize a unit string to a standardized form.

    Args:
        unit: Raw unit string from data
        field_name: Optional canonical field name for inferring unit

    Returns:
        Standardized unit string
    """
    if unit:
        unit_clean = unit.strip()
        if unit_clean in UNIT_NORMALIZATION:
            return UNIT_NORMALIZATION[unit_clean]
        return unit_clean

    # Infer from field name if known
    if field_name:
        field_lower = field_name.lower().replace(" ", "_")
        if field_lower in CANONICAL_FIELD_UNITS:
            return CANONICAL_FIELD_UNITS[field_lower]

    return "dimensionless"


# -----------------------------------------------------------------------------
# LabLink Standard Format
# -----------------------------------------------------------------------------

@dataclass
class Measurement:
    """A single measurement in the standardized format."""
    field: str  # Canonical field name
    original_field: str  # Original column name from raw data
    value: Union[float, int, str, None]
    unit: str = "dimensionless"
    timestamp: Optional[str] = None  # ISO8601 if time-series
    row_index: Optional[int] = None  # Row number in source data
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Source:
    """Source information for the data."""
    instrument: str
    filename: str
    ingested_at: str  # ISO8601
    org_id: str
    format_version: str = ""
    file_size_bytes: int = 0


@dataclass
class Sample:
    """Sample information."""
    id: str
    name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QCInfo:
    """QC summary information."""
    status: str  # "pass", "warn", "fail"
    flags: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Lineage:
    """Data lineage information."""
    raw_s3_key: str
    processing_version: str
    schema_mapping_confidence: float
    transform_version: str = TRANSFORM_VERSION
    transform_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    checksum: str = ""  # Will be computed


@dataclass
class LabLinkStandardFormat:
    """
    LabLink Standard Format (LSF) - our canonical data representation.

    This format provides a consistent structure for all lab data,
    regardless of the source instrument or file format.
    """
    version: str
    source: Source
    sample: Sample
    measurements: List[Measurement]
    qc: QCInfo
    lineage: Lineage

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "source": asdict(self.source),
            "sample": asdict(self.sample),
            "measurements": [asdict(m) for m in self.measurements],
            "qc": asdict(self.qc),
            "lineage": asdict(self.lineage),
        }

    def compute_checksum(self) -> str:
        """Compute a checksum of the data for integrity verification."""
        import json
        # Exclude lineage from checksum (it contains the checksum itself)
        data_for_hash = {
            "version": self.version,
            "source": asdict(self.source),
            "sample": asdict(self.sample),
            "measurements": [asdict(m) for m in self.measurements],
            "qc": asdict(self.qc),
        }
        canonical = json.dumps(data_for_hash, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def transform_to_standard(
    parsed_result: Dict[str, Any],
    schema_mapping: Dict[str, Any],
    qc_result: Dict[str, Any],
    org_id: str,
    s3_key: str,
    ingested_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Transform parsed data to LabLink Standard Format.

    Args:
        parsed_result: Output from parser (ParsedResult.to_dict() or raw dict)
        schema_mapping: Output from schema mapper (with "mapping" dict)
        qc_result: Output from QC module
        org_id: Organization ID
        s3_key: S3 key where raw file is stored
        ingested_at: ISO8601 timestamp of ingestion (defaults to now)

    Returns:
        LabLink Standard Format as a dictionary
    """
    if ingested_at is None:
        ingested_at = datetime.now(timezone.utc).isoformat()

    # Extract source info
    source = Source(
        instrument=parsed_result.get("instrument", "unknown"),
        filename=parsed_result.get("source_file", ""),
        ingested_at=ingested_at,
        org_id=org_id,
        format_version=parsed_result.get("format_version", ""),
        file_size_bytes=parsed_result.get("file_size_bytes", 0),
    )

    # Extract sample info
    metadata = parsed_result.get("metadata", {})
    sample_id = metadata.get("sample_id") or metadata.get("sample_name") or str(uuid.uuid4())[:8]
    sample_name = metadata.get("sample_name", "")

    sample = Sample(
        id=sample_id,
        name=sample_name,
        metadata={
            k: v for k, v in metadata.items()
            if k not in ("sample_id", "sample_name")
        },
    )

    # Build measurements from raw_stats and schema mapping
    mapping = schema_mapping.get("mapping", {})
    raw_stats = parsed_result.get("raw_stats", {})
    measurements = []

    for original_field, field_stats in raw_stats.items():
        # Get canonical field name from mapping
        canonical_field = mapping.get(original_field, original_field)
        if canonical_field == "unknown":
            canonical_field = original_field  # Keep original if unmapped

        values = field_stats.get("values", [])

        # Create a measurement for each value (or aggregate)
        if len(values) <= 10:
            # Few values: include each as separate measurement
            for i, value in enumerate(values):
                measurements.append(Measurement(
                    field=canonical_field,
                    original_field=original_field,
                    value=value,
                    unit=normalize_unit(None, canonical_field),
                    row_index=i,
                ))
        else:
            # Many values: include summary statistics as measurements
            measurements.append(Measurement(
                field=f"{canonical_field}_mean",
                original_field=original_field,
                value=field_stats.get("mean"),
                unit=normalize_unit(None, canonical_field),
                metadata={"aggregation": "mean", "n": field_stats.get("n", 0)},
            ))
            measurements.append(Measurement(
                field=f"{canonical_field}_std",
                original_field=original_field,
                value=field_stats.get("std"),
                unit=normalize_unit(None, canonical_field),
                metadata={"aggregation": "std"},
            ))
            measurements.append(Measurement(
                field=f"{canonical_field}_min",
                original_field=original_field,
                value=field_stats.get("min"),
                unit=normalize_unit(None, canonical_field),
                metadata={"aggregation": "min"},
            ))
            measurements.append(Measurement(
                field=f"{canonical_field}_max",
                original_field=original_field,
                value=field_stats.get("max"),
                unit=normalize_unit(None, canonical_field),
                metadata={"aggregation": "max"},
            ))

    # Build QC info
    qc_flags_raw = qc_result.get("qc_flags", {})
    qc_flags_list = []
    for field_name, field_qc in qc_flags_raw.items():
        for anomaly in field_qc.get("anomalies", []):
            qc_flags_list.append({
                "field": field_name,
                "type": anomaly.get("type"),
                "details": anomaly.get("details", {}),
            })

    qc_info = QCInfo(
        status=qc_result.get("overall_status", "pass"),
        flags=qc_flags_list,
    )

    # Build lineage
    schema_confidence = schema_mapping.get("confidence", 0.0)
    if isinstance(schema_confidence, dict):
        # Average confidence if per-field
        confidences = [v for v in schema_confidence.values() if isinstance(v, (int, float))]
        schema_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    lineage = Lineage(
        raw_s3_key=s3_key,
        processing_version=TRANSFORM_VERSION,
        schema_mapping_confidence=schema_confidence,
    )

    # Assemble the standard format
    lsf = LabLinkStandardFormat(
        version=LSF_VERSION,
        source=source,
        sample=sample,
        measurements=measurements,
        qc=qc_info,
        lineage=lineage,
    )

    # Compute and add checksum
    lsf.lineage.checksum = lsf.compute_checksum()

    return lsf.to_dict()


# -----------------------------------------------------------------------------
# Allotrope Simple Model (ASM) - Partial Implementation
# -----------------------------------------------------------------------------

# ASM context for JSON-LD
ASM_CONTEXT = {
    "@context": {
        "asm": "https://www.allotrope.org/asm/",
        "qudt": "http://qudt.org/schema/qudt/",
        "unit": "http://qudt.org/vocab/unit/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    }
}


def transform_to_asm(
    parsed_result: Dict[str, Any],
    schema_mapping: Optional[Dict[str, Any]] = None,
    org_id: str = "",
) -> Dict[str, Any]:
    """
    Transform parsed data to Allotrope Simple Model (ASM) format.

    This is a partial implementation that covers basic structure.
    Full ASM compliance requires additional ontology mapping.

    ASM is a standardized data format for analytical chemistry data,
    designed for interoperability across different instruments and systems.

    Args:
        parsed_result: Output from parser (ParsedResult.to_dict() or raw dict)
        schema_mapping: Optional schema mapping for field names
        org_id: Organization ID

    Returns:
        ASM-structured dictionary (partial compliance)
    """
    metadata = parsed_result.get("metadata", {})
    raw_stats = parsed_result.get("raw_stats", {})
    mapping = (schema_mapping or {}).get("mapping", {})

    # Build the ASM document
    asm_doc = {
        # JSON-LD context
        "@context": ASM_CONTEXT["@context"],

        # Document metadata
        "@type": "asm:AnalyticalDataDocument",
        "@id": f"urn:lablink:{org_id}:{parsed_result.get('source_file', 'unknown')}",

        # Technique classification
        "asm:technique": _infer_technique(parsed_result.get("instrument", "")),

        # Device/Instrument information
        "asm:deviceDocument": {
            "@type": "asm:DeviceDocument",
            "asm:deviceType": parsed_result.get("instrument", "unknown"),
            "asm:deviceIdentifier": metadata.get("instrument_id", metadata.get("instrument_name", "")),
            "asm:softwareVersion": metadata.get("software_version", parsed_result.get("format_version", "")),
        },

        # Sample information
        "asm:sampleDocument": {
            "@type": "asm:SampleDocument",
            "asm:sampleIdentifier": metadata.get("sample_id", metadata.get("sample_name", "")),
            "asm:sampleName": metadata.get("sample_name", ""),
            "asm:sampleDescription": metadata.get("comments", ""),
        },

        # Measurement metadata
        "asm:measurementDocument": {
            "@type": "asm:MeasurementDocument",
            "asm:measurementTime": _format_asm_datetime(parsed_result.get("timestamp")),
            "asm:operator": metadata.get("operator", ""),
            "asm:methodName": metadata.get("method", ""),
        },

        # Data elements (measurements)
        "asm:dataElement": _build_asm_data_elements(raw_stats, mapping),

        # Processing information
        "asm:processingDocument": {
            "@type": "asm:ProcessingDocument",
            "asm:processingTime": datetime.now(timezone.utc).isoformat(),
            "asm:processingMethod": "LabLink AI Data Pipeline",
            "asm:processingVersion": TRANSFORM_VERSION,
        },
    }

    return asm_doc


def _infer_technique(instrument: str) -> str:
    """Infer the analytical technique from instrument type."""
    instrument_lower = instrument.lower()

    if "hplc" in instrument_lower or "chemstation" in instrument_lower:
        return "asm:LiquidChromatography"
    elif "gc" in instrument_lower:
        return "asm:GasChromatography"
    elif "ms" in instrument_lower or "mass" in instrument_lower:
        return "asm:MassSpectrometry"
    elif "uv" in instrument_lower or "vis" in instrument_lower or "spectro" in instrument_lower:
        return "asm:UVVisSpectroscopy"
    elif "plate" in instrument_lower or "reader" in instrument_lower:
        return "asm:PlateReader"
    elif "nmr" in instrument_lower:
        return "asm:NMRSpectroscopy"
    elif "ir" in instrument_lower or "ftir" in instrument_lower:
        return "asm:InfraredSpectroscopy"
    else:
        return "asm:AnalyticalTechnique"


def _format_asm_datetime(timestamp: Any) -> str:
    """Format timestamp for ASM (ISO8601)."""
    if timestamp is None:
        return ""
    if isinstance(timestamp, str):
        return timestamp
    if isinstance(timestamp, datetime):
        return timestamp.isoformat()
    return str(timestamp)


def _build_asm_data_elements(
    raw_stats: Dict[str, Any],
    mapping: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Build ASM data elements from raw statistics."""
    elements = []

    for original_field, stats in raw_stats.items():
        canonical_field = mapping.get(original_field, original_field)
        unit = normalize_unit(None, canonical_field)

        # Map to QUDT unit if possible
        qudt_unit = _map_to_qudt_unit(unit)

        element = {
            "@type": "asm:DataElement",
            "asm:fieldName": canonical_field,
            "asm:originalFieldName": original_field,
            "asm:dataType": "numeric",
        }

        # Add statistical summary
        if stats.get("mean") is not None:
            element["asm:statisticalAggregate"] = {
                "@type": "asm:StatisticalAggregate",
                "asm:mean": {
                    "@type": "qudt:QuantityValue",
                    "qudt:numericValue": stats.get("mean"),
                    "qudt:unit": qudt_unit,
                },
                "asm:standardDeviation": {
                    "@type": "qudt:QuantityValue",
                    "qudt:numericValue": stats.get("std"),
                    "qudt:unit": qudt_unit,
                },
                "asm:minimum": {
                    "@type": "qudt:QuantityValue",
                    "qudt:numericValue": stats.get("min"),
                    "qudt:unit": qudt_unit,
                },
                "asm:maximum": {
                    "@type": "qudt:QuantityValue",
                    "qudt:numericValue": stats.get("max"),
                    "qudt:unit": qudt_unit,
                },
                "asm:count": stats.get("n", 0),
            }

        # Include raw values if small dataset
        values = stats.get("values", [])
        if values and len(values) <= 50:
            element["asm:dataValues"] = [
                {
                    "@type": "qudt:QuantityValue",
                    "qudt:numericValue": v,
                    "qudt:unit": qudt_unit,
                }
                for v in values
                if v is not None
            ]

        elements.append(element)

    return elements


def _map_to_qudt_unit(unit: str) -> str:
    """Map standardized unit to QUDT URI."""
    qudt_mapping = {
        "min": "unit:MIN",
        "s": "unit:SEC",
        "h": "unit:HR",
        "mg/L": "unit:MilliGM-PER-L",
        "µg/L": "unit:MicroGM-PER-L",
        "ng/mL": "unit:NanoGM-PER-MilliL",
        "µg/mL": "unit:MicroGM-PER-MilliL",
        "mg/mL": "unit:MilliGM-PER-MilliL",
        "g/L": "unit:GM-PER-L",
        "mol/L": "unit:MOL-PER-L",
        "mmol/L": "unit:MilliMOL-PER-L",
        "µmol/L": "unit:MicroMOL-PER-L",
        "nmol/L": "unit:NanoMOL-PER-L",
        "µL": "unit:MicroL",
        "mL": "unit:MilliL",
        "L": "unit:L",
        "ng": "unit:NanoGM",
        "µg": "unit:MicroGM",
        "mg": "unit:MilliGM",
        "g": "unit:GM",
        "kg": "unit:KiloGM",
        "AU": "unit:ABSORBANCE",
        "mAU": "unit:MilliABSORBANCE",
        "°C": "unit:DEG_C",
        "°F": "unit:DEG_F",
        "K": "unit:K",
        "%": "unit:PERCENT",
        "ppm": "unit:PPM",
        "ppb": "unit:PPB",
        "count": "unit:NUM",
        "dimensionless": "unit:UNITLESS",
    }

    return qudt_mapping.get(unit, f"unit:{unit}")


# -----------------------------------------------------------------------------
# Format Registry
# -----------------------------------------------------------------------------

DEFAULT_OUTPUT_FORMAT = "asm"

SUPPORTED_FORMATS = {
    "asm": {
        "name": "Allotrope Simple Model",
        "version": "bioprocess-1",
        "description": "Primary export — ASM-aligned bioprocess records (recommended)",
        "transform_fn": transform_to_asm,
        "default": True,
    },
    "lablink": {
        "name": "LabLink Standard Format (legacy)",
        "version": LSF_VERSION,
        "description": "Legacy JSON schema; prefer ASM for new integrations",
        "transform_fn": transform_to_standard,
        "default": False,
    },
}


def transform_bioprocess_asm(
    run_meta: Dict[str, Any],
    series: List[Dict[str, Any]],
    alignment: Optional[Dict[str, Any]] = None,
    org_id: str = "",
) -> Dict[str, Any]:
    """Build ASM-oriented bioprocess document from run + measurement series."""
    doc = {
        "@context": ASM_CONTEXT["@context"],
        "@type": "asm:BioprocessRunDocument",
        "@id": f"urn:lablink:{org_id}:run:{run_meta.get('external_run_id', 'unknown')}",
        "asm:technique": "asm:Bioprocess",
        "asm:runDocument": {
            "@type": "asm:RunDocument",
            "asm:runIdentifier": run_meta.get("external_run_id"),
            "asm:batchIdentifier": run_meta.get("batch_id"),
            "asm:campaignIdentifier": run_meta.get("campaign_id"),
            "asm:bioreactorIdentifier": run_meta.get("bioreactor_id"),
            "asm:status": run_meta.get("status"),
        },
        "asm:measurementSeries": [],
        "asm:alignmentDocument": alignment or {},
        "asm:processingDocument": {
            "@type": "asm:ProcessingDocument",
            "asm:processingTime": datetime.now(timezone.utc).isoformat(),
            "asm:processingMethod": "LabLink AI Bioprocess Pipeline",
            "asm:processingVersion": TRANSFORM_VERSION,
        },
    }

    for s in series:
        doc["asm:measurementSeries"].append({
            "@type": "asm:MeasurementTimeSeries",
            "asm:fieldName": s.get("canonical_field") or s.get("field_name"),
            "asm:originalFieldName": s.get("field_name"),
            "asm:dataKind": s.get("data_kind", "continuous"),
            "asm:timeUnit": s.get("time_unit", "h"),
            "asm:timeValues": s.get("time_values", []),
            "asm:dataValues": [
                {"@type": "qudt:QuantityValue", "qudt:numericValue": v}
                for v in (s.get("values") or [])
            ],
        })

    return doc


def transform_run_to_asm(db, run) -> Dict[str, Any]:
    """Load series from DB and emit ASM bioprocess document."""
    from database import MeasurementSeries

    series_rows = (
        db.query(MeasurementSeries)
        .filter(MeasurementSeries.run_id == run.id)
        .all()
    )
    series = [
        {
            "field_name": s.field_name,
            "canonical_field": s.canonical_field,
            "data_kind": s.data_kind,
            "time_unit": s.time_unit,
            "time_values": s.time_values,
            "values": s.values,
        }
        for s in series_rows
    ]
    return transform_bioprocess_asm(
        run_meta={
            "external_run_id": run.external_run_id,
            "batch_id": run.batch_id,
            "campaign_id": run.campaign_id,
            "bioreactor_id": run.bioreactor_id,
            "status": run.status,
        },
        series=series,
        alignment=run.alignment,
        org_id=run.org_id,
    )


def list_output_formats() -> List[Dict[str, str]]:
    """List all available output formats (ASM first)."""
    ordered = sorted(
        SUPPORTED_FORMATS.items(),
        key=lambda x: (not x[1].get("default", False), x[0]),
    )
    return [
        {
            "format": key,
            "name": info["name"],
            "version": info["version"],
            "description": info["description"],
            "recommended": info.get("default", False),
        }
        for key, info in ordered
    ]


def transform_data(
    format_name: str,
    parsed_result: Dict[str, Any],
    schema_mapping: Dict[str, Any],
    qc_result: Dict[str, Any],
    org_id: str,
    s3_key: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Transform data to the specified output format.

    Args:
        format_name: Target format ("lablink" or "asm")
        parsed_result: Output from parser
        schema_mapping: Output from schema mapper
        qc_result: Output from QC module
        org_id: Organization ID
        s3_key: S3 key for raw file
        **kwargs: Additional format-specific arguments

    Returns:
        Transformed data in the requested format

    Raises:
        ValueError: If format is not supported
    """
    if format_name not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unknown format: {format_name}. "
            f"Supported formats: {list(SUPPORTED_FORMATS.keys())}"
        )

    if format_name == "lablink":
        return transform_to_standard(
            parsed_result=parsed_result,
            schema_mapping=schema_mapping,
            qc_result=qc_result,
            org_id=org_id,
            s3_key=s3_key,
            **kwargs
        )
    elif format_name == "asm":
        return transform_to_asm(
            parsed_result=parsed_result,
            schema_mapping=schema_mapping,
            org_id=org_id,
        )

    raise ValueError(f"Format {format_name} not implemented")
