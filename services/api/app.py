
import os
import secrets
import asyncio
import uuid
from datetime import datetime, timezone
from contextvars import ContextVar

from fastapi import FastAPI, HTTPException, UploadFile, BackgroundTasks, Query, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import (
    init_db, SessionLocal, FileRecord, Baseline, RunRecord, DataKind,
    AuditLog, AuditAction, EntityType, log_audit, verify_audit_chain,
    WebhookSubscription, WebhookEvent, engine
)
from runs_service import (
    get_or_create_run, persist_measurement_series,
    rebuild_run_alignment, run_qc_for_manifest, update_run_qc,
)
from bioprocess_routes import router as bioprocess_router
from storage import get_presigned_post, ensure_bucket, s3 as s3_client, S3_BUCKET as BUCKET
from mapping import guess_schema
from qc import qc_summary
from webhooks import fire_webhooks_sync, send_test_webhook
from transform import (
    transform_to_standard, transform_to_asm, transform_data,
    list_output_formats, SUPPORTED_FORMATS, DEFAULT_OUTPUT_FORMAT,
)
from database import MeasurementSeries
from baselines import (
    get_baselines, get_baselines_for_qc, get_all_baselines,
    update_baselines, reset_baselines
)
from logging_config import setup_logging, get_logger, LogContext, request_id_ctx, org_id_ctx, file_id_ctx
from exceptions import (
    LabLinkError, ParserError, SchemaMatchError, StorageError,
    ValidationError, DatabaseError, WebhookError, QCError
)
from circuit_breaker import (
    get_all_breaker_status, reset_all_breakers,
    storage_breaker, webhook_breaker, database_breaker
)

# Version info
API_VERSION = "0.2.0-bioprocess"
BUILD_DATE = "2026-05-21"

# Request ID context variable (legacy, prefer request_id_ctx from logging_config)
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Configure structured logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
JSON_LOGS = os.environ.get("JSON_LOGS", "true").lower() == "true"
setup_logging(level=LOG_LEVEL, service_name="lablink-api", json_output=JSON_LOGS)
logger = get_logger(__name__)


# OpenAPI documentation
API_DESCRIPTION = """
# LabLink AI API

**Bioprocess Data Platform for CDMOs and Process Development**

LabLink AI ingests bioreactor controller logs and offline analytics (HPLC titer,
Vi-CELL, Nova BioProfile), aligns them on a run timeline, and exports
**Allotrope Simple Model (ASM)** records with GxP-ready audit trails.

## Key Features

- **Run-centric model**: Batch/campaign runs with queryable time-series in PostgreSQL
- **Bioprocess parsers**: Sartorius, Eppendorf, Cytiva, Nova, Vi-CELL, Agilent HPLC
- **Domain QC**: VCD growth, DO/pH setpoint excursions, titer trajectories
- **ASM-first export**: ASM default; LabLink Standard Format (legacy) optional
- **Read-only dashboard**: `/dashboard` for process scientists
- **API keys**: `X-API-Key` header; set `AUTH_REQUIRED=true` in production

## Authentication

Use `X-API-Key` from `POST /api/v1/auth/keys`. When `AUTH_REQUIRED=false` (dev),
`org_id` query parameter is accepted.

## Getting Started

1. Get a presigned URL: `POST /api/v1/presign`
2. Upload your file to S3 using the presigned URL
3. Submit the manifest: `POST /api/v1/events`
4. Query processed files: `GET /api/v1/files`

## Support

For issues and feature requests, visit the GitHub repository.
"""

app = FastAPI(
    title="LabLink AI",
    description=API_DESCRIPTION,
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "Files", "description": "File upload and processing operations"},
        {"name": "QC & Baselines", "description": "Quality control and historical baselines"},
        {"name": "Transformation", "description": "Data format transformation"},
        {"name": "Webhooks", "description": "Webhook subscription management"},
        {"name": "Audit", "description": "Audit logging and compliance"},
        {"name": "System", "description": "Health checks and system information"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(bioprocess_router)


# Request ID Middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add unique request ID to each request for tracing."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request_id_var.set(request_id)

    # Add to request state for access in endpoints
    request.state.request_id = request_id

    # Log request
    logger.info(f"[{request_id}] {request.method} {request.url.path}")

    response = await call_next(request)

    # Add request ID to response headers
    response.headers["X-Request-ID"] = request_id

    return response


def get_db():
    """Dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def startup():
    # Schema is managed by Alembic (make migrate). create_all() is not called here
    # to avoid drift vs migration history — use `make migrate-repair` on legacy DBs.
    ensure_bucket()
    try:
        from startup_checks import check_bioprocess_schema
        check_bioprocess_schema()
    except Exception:
        pass


class PresignRequest(BaseModel):
    """Request for a presigned S3 upload URL."""
    filename: str = Field(..., description="Name of the file to upload", example="sample_data.csv")
    org_id: str = Field("default-org", description="Organization identifier for data isolation")

    class Config:
        json_schema_extra = {
            "example": {
                "filename": "hplc_run_001.csv",
                "org_id": "acme-pharma"
            }
        }


class PresignResponse(BaseModel):
    """Presigned URL and form fields for S3 upload."""
    url: str = Field(..., description="S3 endpoint URL for upload")
    fields: Dict[str, Any] = Field(..., description="Form fields to include in the upload request")

    class Config:
        json_schema_extra = {
            "example": {
                "url": "http://localhost:9000/lablink-data",
                "fields": {
                    "key": "data/acme-pharma/hplc_run_001.csv",
                    "policy": "base64-encoded-policy",
                    "signature": "signature-string"
                }
            }
        }


@app.post(
    "/api/v1/presign",
    response_model=PresignResponse,
    tags=["Files"],
    summary="Get presigned upload URL",
    description="""
Generate a presigned URL for uploading a file directly to S3/MinIO.

The returned URL and fields should be used to construct a multipart form POST
request to upload the file. The presigned URL is valid for 1 hour.

**Example upload with curl:**
```bash
curl -X POST "${url}" \\
  -F "key=${fields.key}" \\
  -F "policy=${fields.policy}" \\
  -F "signature=${fields.signature}" \\
  -F "file=@/path/to/your/file.csv"
```
""",
    responses={
        200: {"description": "Presigned URL generated successfully"},
        500: {"description": "Storage service error"},
    },
)
def presign(req: PresignRequest, db: Session = Depends(get_db)):
    try:
        url, fields = get_presigned_post(req.filename, org_id=req.org_id)

        # Audit log: presign URL generated
        log_audit(
            action=AuditAction.PRESIGN_GENERATED,
            entity_type=EntityType.FILE,
            entity_id=req.filename,
            actor="api",
            org_id=req.org_id,
            details={
                "filename": req.filename,
                "s3_key": fields.get("key"),
            },
            db=db,
        )

        return PresignResponse(url=url, fields=fields)
    except Exception as e:
        logger.error(f"[{request_id_var.get()}] Presign error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class Manifest(BaseModel):
    """
    File manifest submitted after upload.

    Contains metadata about the uploaded file and optional parsed data
    from the edge agent.
    """
    org_id: str = Field("default-org", description="Organization identifier")
    filename: str = Field(..., description="Original filename")
    s3_key: str = Field(..., description="S3 key where file is stored")
    size: Optional[int] = Field(None, description="File size in bytes")
    sample_id: Optional[str] = Field(None, description="Sample identifier from metadata")
    instrument: Optional[str] = Field(
        None,
        description="Instrument type (e.g., 'agilent_chemstation', 'generic_csv')"
    )
    headers: Optional[List[str]] = Field(
        None,
        description="Column headers extracted from the file"
    )
    stats: Optional[Dict[str, Any]] = Field(
        None,
        description="Per-column statistics for QC (mean, std, min, max, values)"
    )
    parsed_result: Optional[Dict[str, Any]] = Field(
        None,
        description="Full parsed result from instrument parser"
    )
    output_format: Optional[str] = Field(
        None,
        description="Output format: 'asm' (default) or 'lablink' (legacy)"
    )
    run_id: Optional[int] = Field(None, description="Existing run ID to attach file")
    external_run_id: Optional[str] = Field(None, description="Run/batch identifier")
    batch_id: Optional[str] = None
    campaign_id: Optional[str] = None
    bioreactor_id: Optional[str] = None
    data_kind: Optional[str] = Field(
        "continuous",
        description="continuous | discrete_offline",
    )
    time_column: Optional[str] = None
    series_points: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Time-aligned points [{t, field, value}] for DB persistence",
    )
    parsed_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Parser metadata from edge agent",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "org_id": "acme-cdmo",
                "external_run_id": "RUN-2026-0142",
                "filename": "biostat_run.csv",
                "s3_key": "data/acme-pharma/caffeine_standard.csv",
                "size": 2048,
                "sample_id": "SAMPLE001",
                "instrument": "agilent_chemstation",
                "headers": ["Retention_Time", "Area", "Height", "Compound"],
                "stats": {
                    "Retention_Time": {
                        "mean": 5.0,
                        "std": 2.5,
                        "min": 1.2,
                        "max": 9.0,
                        "values": [1.2, 2.5, 4.1, 5.9, 7.2, 9.0]
                    }
                }
            }
        }


class FileOut(BaseModel):
    """Processed file record."""
    id: int = Field(..., description="Unique file ID")
    org_id: str = Field(..., description="Organization identifier")
    filename: str = Field(..., description="Original filename")
    s3_key: str = Field(..., description="S3 storage key")
    schema_guess: Dict[str, Any] = Field(..., description="Schema mapping results")
    qc: Dict[str, Any] = Field(..., description="Quality control results")

    class Config:
        json_schema_extra = {
            "example": {
                "id": 1,
                "org_id": "acme-pharma",
                "filename": "caffeine_standard.csv",
                "s3_key": "data/acme-pharma/caffeine_standard.csv",
                "schema_guess": {
                    "mapping": {
                        "Retention_Time": "retention_time",
                        "Area": "peak_area"
                    },
                    "confidence": 0.87
                },
                "qc": {
                    "overall_status": "pass",
                    "summary": "All QC checks passed."
                }
            }
        }


class EventResponse(BaseModel):
    """Response from file processing endpoint."""
    status: str = Field(..., description="Processing status: 'accepted' or 'processed'")
    file_id: Optional[int] = Field(None, description="Created file record ID")
    transformed_data: Optional[Dict[str, Any]] = Field(
        None,
        description="Transformed data (if output_format was specified)"
    )
    format: Optional[str] = Field(None, description="Output format used")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "processed",
                "file_id": 42,
                "format": "lablink",
                "transformed_data": {
                    "version": "1.0",
                    "source": {"instrument": "agilent_chemstation"}
                }
            }
        }


# --- Health Check Models ---

class ServiceStatus(BaseModel):
    """Status of an individual service component."""
    status: str = Field(..., description="'healthy' or 'unhealthy'")
    latency_ms: Optional[float] = Field(None, description="Response time in milliseconds")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class HealthResponse(BaseModel):
    """System health check response."""
    status: str = Field(..., description="Overall status: 'healthy', 'degraded', or 'unhealthy'")
    version: str = Field(..., description="API version")
    build_date: str = Field(..., description="Build date")
    timestamp: str = Field(..., description="Current server time (ISO8601)")
    services: Dict[str, ServiceStatus] = Field(..., description="Individual service statuses")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "version": "0.1.0",
                "build_date": "2024-01-15",
                "timestamp": "2024-01-15T10:30:00Z",
                "services": {
                    "database": {"status": "healthy", "latency_ms": 2.5},
                    "storage": {"status": "healthy", "latency_ms": 15.3}
                }
            }
        }


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str = Field(..., description="Error message")
    request_id: Optional[str] = Field(None, description="Request ID for debugging")


def process_manifest(m: Manifest, return_transformed: bool = False) -> Optional[Dict[str, Any]]:
    """
    Process file manifest with full audit logging and webhook notifications.

    Args:
        m: The manifest to process
        return_transformed: If True, return transformation result

    Returns:
        Transformed data dict if return_transformed=True and output_format specified,
        otherwise None

    Note:
        This function uses comprehensive error handling to ensure partial failures
        don't crash the processing pipeline. Errors in schema mapping, QC, webhooks,
        or baselines are logged but processing continues where possible.
    """
    file_id = None
    db = None

    # Set logging context
    org_id_ctx.set(m.org_id)

    try:
        # Step 1: Schema mapping (non-critical - use empty mapping on failure)
        try:
            schema = guess_schema(m.headers or [])
            logger.info(f"Schema mapping complete for {m.filename}", extra={
                "source_filename": m.filename,
                "confidence": schema.get("confidence"),
            })
        except Exception as e:
            logger.error(f"Schema mapping failed for {m.filename}: {e}", extra={
                "source_filename": m.filename,
                "error_type": type(e).__name__,
            })
            # Use empty schema - don't fail the entire process
            schema = {"mapping": {}, "confidence": {}, "error": str(e)}

        # Step 2: Database operations
        db = SessionLocal()

        try:
            # Fetch historical baselines for drift detection
            instrument = m.instrument or "unknown"

            # Resolve bioprocess run
            run = None
            parsed_meta = getattr(m, "parsed_metadata", None) or {}
            external_id = (
                m.external_run_id
                or m.batch_id
                or parsed_meta.get("run_external_id")
            )
            if m.run_id:
                run = db.query(RunRecord).filter(
                    RunRecord.id == m.run_id, RunRecord.org_id == m.org_id
                ).first()
            elif external_id:
                run = get_or_create_run(
                    db, m.org_id, str(external_id),
                    batch_id=m.batch_id, campaign_id=m.campaign_id,
                    bioreactor_id=m.bioreactor_id,
                )

            # Step 3: Run QC (non-critical - use pass status on failure)
            try:
                qc = run_qc_for_manifest(
                    stats=m.stats or {},
                    org_id=m.org_id,
                    instrument=instrument,
                    db=db,
                )
                logger.info(f"QC complete for {m.filename}: {qc.get('overall_status', 'unknown')}")
            except Exception as e:
                logger.error(f"QC analysis failed for {m.filename}: {e}", extra={
                    "source_filename": m.filename,
                    "error_type": type(e).__name__,
                })
                # Use default pass status - don't fail the entire process
                qc = {
                    "overall_status": "unknown",
                    "summary": f"QC analysis failed: {e}",
                    "qc_flags": {},
                    "error": str(e),
                }

            # Step 4: Create file record (critical - must succeed)
            try:
                data_kind = m.data_kind or DataKind.CONTINUOUS.value
                rec = FileRecord(
                    org_id=m.org_id,
                    run_id=run.id if run else None,
                    filename=m.filename,
                    s3_key=m.s3_key,
                    sample_id=m.sample_id,
                    instrument=m.instrument,
                    data_kind=data_kind,
                    schema_guess=schema,
                    qc=qc,
                )
                db.add(rec)
                db.commit()
                db.refresh(rec)
                file_id = str(rec.id)
                file_id_ctx.set(file_id)
                logger.info(f"File record created: {file_id}")

                if run:
                    persist_measurement_series(
                        db, m.org_id, run.id, rec.id,
                        stats=m.stats or {},
                        schema_mapping=schema,
                        data_kind=data_kind,
                        series_points=m.series_points,
                        time_column=m.time_column,
                    )
                    rebuild_run_alignment(db, run.id)
                    update_run_qc(db, run.id, m.org_id, m.instrument)
            except SQLAlchemyError as e:
                logger.error(f"Database error creating file record: {e}", extra={
                    "source_filename": m.filename,
                    "error_type": type(e).__name__,
                })
                db.rollback()
                raise DatabaseError(
                    message=f"Failed to create file record: {e}",
                    operation="insert",
                    table="file_records",
                    cause=e,
                )

            # Step 5: Audit logging (non-critical)
            try:
                log_audit(
                    action=AuditAction.FILE_INGESTED,
                    entity_type=EntityType.FILE,
                    entity_id=file_id,
                    actor="edge-agent",
                    org_id=m.org_id,
                    details={
                        "filename": m.filename,
                        "s3_key": m.s3_key,
                        "size": m.size,
                        "sample_id": m.sample_id,
                        "instrument": m.instrument,
                        "header_count": len(m.headers) if m.headers else 0,
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Audit logging failed (FILE_INGESTED): {e}")

            # Step 6: Webhooks (non-critical - don't fail processing on webhook errors)
            try:
                fire_webhooks_sync(
                    org_id=m.org_id,
                    event_type=WebhookEvent.FILE_INGESTED,
                    data={
                        "file_id": file_id,
                        "filename": m.filename,
                        "s3_key": m.s3_key,
                        "size": m.size,
                        "sample_id": m.sample_id,
                        "instrument": m.instrument,
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Webhook delivery failed (file.ingested): {e}")

            # Audit: Schema mapped
            try:
                log_audit(
                    action=AuditAction.SCHEMA_MAPPED,
                    entity_type=EntityType.FILE,
                    entity_id=file_id,
                    actor="api",
                    org_id=m.org_id,
                    details={
                        "mapping": schema.get("mapping"),
                        "confidence": schema.get("confidence"),
                        "unmapped_fields": [
                            h for h, v in schema.get("mapping", {}).items()
                            if v == "unknown"
                        ],
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Audit logging failed (SCHEMA_MAPPED): {e}")

            # Webhook: schema.mapped
            try:
                fire_webhooks_sync(
                    org_id=m.org_id,
                    event_type=WebhookEvent.SCHEMA_MAPPED,
                    data={
                        "file_id": file_id,
                        "filename": m.filename,
                        "mapping": schema.get("mapping"),
                        "confidence": schema.get("confidence"),
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Webhook delivery failed (schema.mapped): {e}")

            # Audit: QC completed
            qc_flags = qc.get("qc_flags", {})
            overall_status = qc.get("overall_status", "pass")
            has_anomalies = overall_status in ("warn", "fail")

            try:
                log_audit(
                    action=AuditAction.QC_COMPLETED,
                    entity_type=EntityType.FILE,
                    entity_id=file_id,
                    actor="api",
                    org_id=m.org_id,
                    details={
                        "has_anomalies": has_anomalies,
                        "overall_status": overall_status,
                        "summary": qc.get("summary"),
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Audit logging failed (QC_COMPLETED): {e}")

            # Webhook: qc.completed
            try:
                fire_webhooks_sync(
                    org_id=m.org_id,
                    event_type=WebhookEvent.QC_COMPLETED,
                    data={
                        "file_id": file_id,
                        "filename": m.filename,
                        "overall_status": overall_status,
                        "summary": qc.get("summary"),
                        "qc_flags": qc_flags,
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Webhook delivery failed (qc.completed): {e}")

            # Audit and webhook: Flag individual anomalies if found
            if has_anomalies:
                anomaly_details = []
                for field_name, field_data in qc_flags.items():
                    field_anomalies = field_data.get("anomalies", [])
                    if field_anomalies:
                        try:
                            log_audit(
                                action=AuditAction.QC_ANOMALY_FLAGGED,
                                entity_type=EntityType.FILE,
                                entity_id=file_id,
                                actor="api",
                                org_id=m.org_id,
                                details={
                                    "field": field_name,
                                    "anomaly_count": len(field_anomalies),
                                    "anomalies": field_anomalies[:10],
                                    "stats": field_data.get("stats"),
                                },
                                db=db,
                            )
                        except Exception as e:
                            logger.error(f"Audit logging failed (QC_ANOMALY_FLAGGED): {e}")

                        anomaly_details.append({
                            "field": field_name,
                            "status": field_data.get("status"),
                            "anomaly_count": len(field_anomalies),
                            "anomaly_types": list(set(a["type"] for a in field_anomalies)),
                        })

                # Webhook: qc.anomaly_detected
                try:
                    fire_webhooks_sync(
                        org_id=m.org_id,
                        event_type=WebhookEvent.QC_ANOMALY_DETECTED,
                        data={
                            "file_id": file_id,
                            "filename": m.filename,
                            "overall_status": overall_status,
                            "anomalies": anomaly_details,
                        },
                        db=db,
                    )
                except Exception as e:
                    logger.error(f"Webhook delivery failed (qc.anomaly_detected): {e}")

            # Webhook: file.processed (final event after all processing)
            try:
                fire_webhooks_sync(
                    org_id=m.org_id,
                    event_type=WebhookEvent.FILE_PROCESSED,
                    data={
                        "file_id": file_id,
                        "filename": m.filename,
                        "s3_key": m.s3_key,
                        "schema_mapping": schema.get("mapping"),
                        "qc_status": overall_status,
                        "qc_summary": qc.get("summary"),
                    },
                    db=db,
                )
            except Exception as e:
                logger.error(f"Webhook delivery failed (file.processed): {e}")

            # Step 7: Update baselines (non-critical)
            # Only update if QC passed - don't let anomalous data corrupt baselines
            if overall_status == "pass" and m.stats:
                try:
                    updated_baselines = update_baselines(
                        org_id=m.org_id,
                        instrument=instrument,
                        new_stats=m.stats,
                        db=db,
                    )

                    if updated_baselines:
                        try:
                            log_audit(
                                action=AuditAction.BASELINE_UPDATED,
                                entity_type=EntityType.BASELINE,
                                entity_id=f"{m.org_id}:{instrument}",
                                actor="api",
                                org_id=m.org_id,
                                details={
                                    "instrument": instrument,
                                    "fields_updated": list(updated_baselines.keys()),
                                    "file_id": file_id,
                                },
                                db=db,
                            )
                        except Exception as e:
                            logger.error(f"Audit logging failed (BASELINE_UPDATED): {e}")
                except Exception as e:
                    logger.error(f"Baseline update failed: {e}", extra={
                        "source_filename": m.filename,
                        "instrument": instrument,
                    })

            # Step 8: Transform data if requested
            if return_transformed and m.output_format:
                try:
                    # Build parsed_result from manifest or use provided
                    parsed_result = m.parsed_result or {
                        "instrument": m.instrument or "unknown",
                        "format_version": "1.0",
                        "timestamp": None,
                        "metadata": {
                            "sample_id": m.sample_id,
                        },
                        "headers": m.headers or [],
                        "raw_stats": m.stats or {},
                        "source_file": m.filename,
                        "file_size_bytes": m.size or 0,
                    }

                    transformed = transform_data(
                        format_name=m.output_format,
                        parsed_result=parsed_result,
                        schema_mapping=schema,
                        qc_result=qc,
                        org_id=m.org_id,
                        s3_key=m.s3_key,
                    )
                    logger.info(f"Data transformed to {m.output_format} format")
                    return {"file_id": int(file_id), "transformed": transformed}
                except Exception as e:
                    logger.error(f"Data transformation failed: {e}", extra={
                        "source_filename": m.filename,
                        "format": m.output_format,
                    })
                    # Return file_id without transformation
                    return {"file_id": int(file_id), "transform_error": str(e)}

            return {"file_id": int(file_id)}

        except SQLAlchemyError as e:
            if db:
                db.rollback()
            raise

    except LabLinkError:
        # Re-raise our custom exceptions
        raise
    except Exception as e:
        # Log unexpected errors
        logger.exception(f"Unexpected error processing manifest for {m.filename}: {e}")
        raise
    finally:
        if db:
            db.close()
        # Clear context variables
        file_id_ctx.set("")
        org_id_ctx.set("")


def _run_process_manifest_safe(m: Manifest, return_transformed: bool = False):
    """Background wrapper so failures are logged (async path returns no error)."""
    try:
        return process_manifest(m, return_transformed=return_transformed)
    except Exception as e:
        logger.exception(f"Background manifest processing failed for {m.filename}: {e}")
        raise


@app.post(
    "/api/v1/events",
    response_model=EventResponse,
    tags=["Files"],
    summary="Process file manifest",
    description="""
Submit a file manifest for processing after uploading to S3.

Use **sync=true** during development to run inline and surface errors in the response.
""",
    responses={
        200: {"description": "File accepted or processed"},
        400: {"description": "Invalid manifest or output format"},
        500: {"description": "Processing failed (sync mode only)"},
    },
)
def events(
    m: Manifest,
    bg: BackgroundTasks,
    sync: bool = Query(
        False,
        description="Process inline and return errors (recommended for local testing)",
    ),
):
    """Process uploaded file manifest with schema mapping and QC."""
    if m.output_format and m.output_format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output_format: {m.output_format}. "
                   f"Supported formats: {list(SUPPORTED_FORMATS.keys())}"
        )

    if m.output_format or sync:
        try:
            result = _run_process_manifest_safe(m, return_transformed=bool(m.output_format))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return EventResponse(
            status="processed",
            file_id=result.get("file_id") if result else None,
            transformed_data=result.get("transformed") if result else None,
            format=m.output_format,
        )

    bg.add_task(_run_process_manifest_safe, m)
    return EventResponse(status="accepted")


@app.get("/api/v1/files", response_model=List[FileOut])
def list_files(org_id: str = Query("default-org"), db: Session = Depends(get_db)):
    rows = db.query(FileRecord).filter(FileRecord.org_id == org_id).order_by(FileRecord.id.desc()).all()
    out = []
    for r in rows:
        # Audit: File accessed
        log_audit(
            action=AuditAction.FILE_ACCESSED,
            entity_type=EntityType.FILE,
            entity_id=str(r.id),
            actor="api",
            org_id=org_id,
            details={"filename": r.filename, "access_type": "list"},
            db=db,
        )
        out.append(FileOut(
            id=r.id,
            org_id=r.org_id,
            filename=r.filename,
            s3_key=r.s3_key,
            schema_guess=r.schema_guess or {},
            qc=r.qc or {},
        ))
    return out


@app.get("/api/v1/files/{file_id}/normalized")
def get_normalized_file(
    file_id: int,
    format: str = Query(DEFAULT_OUTPUT_FORMAT, description="Output format: asm (default) or lablink"),
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """
    Get the normalized/transformed version of a processed file.

    Returns the file data in a standardized format suitable for
    consumption by downstream systems.

    Supported formats:
    - asm: Allotrope Simple Model bioprocess export (default, recommended)
    - lablink: Legacy LabLink Standard Format
    """
    # Validate format
    if format not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format: {format}. "
                   f"Supported formats: {list(SUPPORTED_FORMATS.keys())}"
        )

    # Fetch the file record
    record = db.query(FileRecord).filter(
        FileRecord.id == file_id,
        FileRecord.org_id == org_id,
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="File not found")

    # Audit: File accessed
    log_audit(
        action=AuditAction.FILE_ACCESSED,
        entity_type=EntityType.FILE,
        entity_id=str(file_id),
        actor="api",
        org_id=org_id,
        details={"filename": record.filename, "access_type": "normalized", "format": format},
        db=db,
    )

    # Build parsed_result from stored data
    # Note: In a full implementation, we'd store the raw parsed result
    # For now, reconstruct from what we have
    qc_data = record.qc or {}
    schema_data = record.schema_guess or {}

    # Prefer persisted measurement series (full trajectories)
    raw_stats = {}
    if record.run_id:
        series_rows = (
            db.query(MeasurementSeries)
            .filter(MeasurementSeries.file_id == file_id)
            .all()
        )
        for s in series_rows:
            key = s.canonical_field or s.field_name
            raw_stats[key] = {
                "mean": float(sum(s.values) / len(s.values)) if s.values else None,
                "std": None,
                "min": min(s.values) if s.values else None,
                "max": max(s.values) if s.values else None,
                "n": len(s.values or []),
                "values": list(s.values or []),
                "time_values": list(s.time_values or []),
                "data_kind": s.data_kind,
            }

    if not raw_stats:
        qc_flags = qc_data.get("qc_flags", {})
        for field_name, field_data in qc_flags.items():
            stats = field_data.get("stats", {})
            raw_stats[field_name] = {
                "mean": stats.get("mean"),
                "std": stats.get("std"),
                "min": stats.get("min"),
                "max": stats.get("max"),
                "n": stats.get("n", 0),
                "values": [],
            }

    parsed_result = {
        "instrument": record.instrument or "unknown",
        "format_version": "1.0",
        "timestamp": None,
        "metadata": {
            "sample_id": record.sample_id,
        },
        "headers": list(schema_data.get("mapping", {}).keys()),
        "raw_stats": raw_stats,
        "source_file": record.filename,
        "file_size_bytes": 0,
    }

    # Transform to requested format
    transformed = transform_data(
        format_name=format,
        parsed_result=parsed_result,
        schema_mapping=schema_data,
        qc_result=qc_data,
        org_id=org_id,
        s3_key=record.s3_key,
    )

    return {
        "file_id": file_id,
        "format": format,
        "data": transformed,
    }


@app.get("/api/v1/formats")
def get_output_formats():
    """
    List available output formats for data transformation.

    These formats can be used with:
    - POST /api/v1/events (output_format parameter)
    - GET /api/v1/files/{id}/normalized (format parameter)
    """
    return {
        "formats": list_output_formats(),
    }


# --- Audit Log Endpoints ---

class AuditLogOut(BaseModel):
    id: int
    timestamp: datetime
    org_id: str
    action: str
    entity_type: str
    entity_id: str
    actor: str
    details: Optional[Dict[str, Any]]
    previous_hash: Optional[str]
    record_hash: str


class AuditVerifyResponse(BaseModel):
    valid: bool
    record_count: int
    errors: List[Dict[str, Any]]


@app.get("/api/v1/audit", response_model=List[AuditLogOut])
def list_audit_logs(
    org_id: str = Query("default-org"),
    start_date: Optional[datetime] = Query(None, description="Filter logs from this date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Filter logs until this date (ISO format)"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Retrieve audit logs for an organization with optional filtering.

    Supports date range filtering, action type filtering, and pagination.
    Results are ordered by timestamp descending (newest first).
    """
    query = db.query(AuditLog).filter(AuditLog.org_id == org_id)

    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.filter(AuditLog.timestamp <= end_date)
    if action:
        try:
            action_enum = AuditAction(action)
            query = query.filter(AuditLog.action == action_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action type. Valid values: {[a.value for a in AuditAction]}"
            )
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)

    records = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()

    return [
        AuditLogOut(
            id=r.id,
            timestamp=r.timestamp,
            org_id=r.org_id,
            action=r.action.value,
            entity_type=r.entity_type.value,
            entity_id=r.entity_id,
            actor=r.actor,
            details=r.details,
            previous_hash=r.previous_hash,
            record_hash=r.record_hash,
        )
        for r in records
    ]


@app.get("/api/v1/audit/verify", response_model=AuditVerifyResponse)
def verify_audit(org_id: str = Query("default-org"), db: Session = Depends(get_db)):
    """
    Verify the integrity of the audit chain for an organization.

    Checks that:
    1. Each record's previous_hash matches the prior record's record_hash
    2. Each record's record_hash correctly represents its contents

    Returns verification status and any detected integrity issues.
    """
    result = verify_audit_chain(org_id, db)
    return AuditVerifyResponse(**result)


@app.get(
    "/healthz",
    tags=["System"],
    summary="Simple health check",
    description="Lightweight health check for load balancers. Use /api/v1/health for detailed status.",
)
def health_simple():
    """Simple health check endpoint for Kubernetes/load balancer probes."""
    return {"ok": True}


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Detailed health check",
    description="""
Comprehensive health check that verifies connectivity to all dependent services.

Returns status for:
- **database**: PostgreSQL connectivity and query performance
- **storage**: S3/MinIO bucket accessibility

Overall status:
- `healthy`: All services operational
- `degraded`: Some services have issues but API is functional
- `unhealthy`: Critical services are down
""",
)
def health_detailed():
    """Detailed health check with service connectivity verification."""
    services = {}
    overall_healthy = True

    # Check database
    try:
        import time
        start = time.time()
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        latency = (time.time() - start) * 1000
        services["database"] = ServiceStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as e:
        services["database"] = ServiceStatus(status="unhealthy", error=str(e))
        overall_healthy = False

    # Check S3/MinIO storage
    try:
        import time
        start = time.time()
        s3_client.head_bucket(Bucket=BUCKET)
        latency = (time.time() - start) * 1000
        services["storage"] = ServiceStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as e:
        services["storage"] = ServiceStatus(status="unhealthy", error=str(e))
        overall_healthy = False

    # Determine overall status
    unhealthy_count = sum(1 for s in services.values() if s.status == "unhealthy")
    if unhealthy_count == 0:
        overall_status = "healthy"
    elif unhealthy_count < len(services):
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return HealthResponse(
        status=overall_status,
        version=API_VERSION,
        build_date=BUILD_DATE,
        timestamp=datetime.now(timezone.utc).isoformat(),
        services=services,
    )


# --- Webhook Endpoints ---

class WebhookCreate(BaseModel):
    """Request to create a webhook subscription."""
    url: str = Field(..., description="URL to POST webhook events to")
    events: List[str] = Field(..., description="Event types to subscribe to")
    org_id: str = "default-org"


class WebhookOut(BaseModel):
    """Webhook subscription response."""
    id: int
    org_id: str
    url: str
    events: List[str]
    secret: str
    active: bool
    created_at: datetime
    last_triggered_at: Optional[datetime]
    failure_count: int


class WebhookTestResult(BaseModel):
    """Result of a webhook test."""
    success: bool
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/v1/webhooks", response_model=WebhookOut)
def create_webhook(req: WebhookCreate, db: Session = Depends(get_db)):
    """
    Register a new webhook subscription.

    The webhook will receive POST requests for the specified event types.
    A secret is auto-generated for HMAC signature verification.

    Supported events:
    - file.ingested: When a new file is uploaded
    - file.processed: When file processing is complete
    - schema.mapped: When schema mapping is complete
    - qc.completed: When QC analysis is complete
    - qc.anomaly_detected: When QC finds anomalies
    """
    # Validate event types
    valid_events = [e.value for e in WebhookEvent]
    for event in req.events:
        if event not in valid_events:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event type: {event}. Valid events: {valid_events}"
            )

    # Generate secret
    secret = secrets.token_urlsafe(32)

    # Create subscription
    subscription = WebhookSubscription(
        org_id=req.org_id,
        url=req.url,
        events=req.events,
        secret=secret,
        active=True,
        failure_count=0,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    # Audit log
    log_audit(
        action=AuditAction.WEBHOOK_REGISTERED,
        entity_type=EntityType.WEBHOOK,
        entity_id=str(subscription.id),
        actor="api",
        org_id=req.org_id,
        details={
            "url": req.url,
            "events": req.events,
        },
        db=db,
    )

    return WebhookOut(
        id=subscription.id,
        org_id=subscription.org_id,
        url=subscription.url,
        events=subscription.events,
        secret=subscription.secret,
        active=subscription.active,
        created_at=subscription.created_at,
        last_triggered_at=subscription.last_triggered_at,
        failure_count=subscription.failure_count,
    )


@app.get("/api/v1/webhooks", response_model=List[WebhookOut])
def list_webhooks(
    org_id: str = Query("default-org"),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List all webhook subscriptions for an organization."""
    query = db.query(WebhookSubscription).filter(
        WebhookSubscription.org_id == org_id
    )

    if not include_inactive:
        query = query.filter(WebhookSubscription.active == True)

    subscriptions = query.order_by(WebhookSubscription.created_at.desc()).all()

    return [
        WebhookOut(
            id=s.id,
            org_id=s.org_id,
            url=s.url,
            events=s.events,
            secret=s.secret,
            active=s.active,
            created_at=s.created_at,
            last_triggered_at=s.last_triggered_at,
            failure_count=s.failure_count,
        )
        for s in subscriptions
    ]


@app.delete("/api/v1/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """Delete a webhook subscription."""
    subscription = db.query(WebhookSubscription).filter(
        WebhookSubscription.id == webhook_id,
        WebhookSubscription.org_id == org_id,
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Webhook not found")

    # Audit log before deletion
    log_audit(
        action=AuditAction.WEBHOOK_DELETED,
        entity_type=EntityType.WEBHOOK,
        entity_id=str(webhook_id),
        actor="api",
        org_id=org_id,
        details={
            "url": subscription.url,
            "events": subscription.events,
        },
        db=db,
    )

    db.delete(subscription)
    db.commit()

    return {"status": "deleted", "id": webhook_id}


@app.post("/api/v1/webhooks/{webhook_id}/test", response_model=WebhookTestResult)
async def test_webhook(
    webhook_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """
    Send a test event to a webhook endpoint.

    This helps verify that the webhook is correctly configured
    and the receiving endpoint is accessible.
    """
    subscription = db.query(WebhookSubscription).filter(
        WebhookSubscription.id == webhook_id,
        WebhookSubscription.org_id == org_id,
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Webhook not found")

    result = await send_test_webhook(subscription, db)

    return WebhookTestResult(**result)


@app.patch("/api/v1/webhooks/{webhook_id}/activate")
def activate_webhook(
    webhook_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """
    Reactivate a deactivated webhook.

    Webhooks are automatically deactivated after 10 consecutive failures.
    Use this endpoint to reactivate them after fixing the issue.
    """
    subscription = db.query(WebhookSubscription).filter(
        WebhookSubscription.id == webhook_id,
        WebhookSubscription.org_id == org_id,
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="Webhook not found")

    subscription.active = True
    subscription.failure_count = 0
    db.commit()

    return {"status": "activated", "id": webhook_id}


@app.get("/api/v1/webhooks/events")
def list_webhook_events():
    """List all available webhook event types."""
    return {
        "events": [
            {
                "name": e.value,
                "description": {
                    "file.ingested": "Fired when a new file is uploaded to the system",
                    "file.processed": "Fired when all processing (schema, QC) is complete",
                    "schema.mapped": "Fired when column headers are mapped to canonical fields",
                    "qc.completed": "Fired when quality control analysis is complete",
                    "qc.anomaly_detected": "Fired when QC finds anomalies (warn or fail status)",
                }.get(e.value, "")
            }
            for e in WebhookEvent
        ]
    }


# --- Baseline Endpoints ---

class BaselineOut(BaseModel):
    """Baseline statistics for a field."""
    field_name: str
    mean: float
    std: float
    n_samples: int
    last_updated: Optional[datetime]
    created_at: Optional[datetime]


class BaselineResetRequest(BaseModel):
    """Request to reset baselines."""
    instrument: str
    field_names: Optional[List[str]] = None  # None = reset all fields


@app.get("/api/v1/baselines")
def get_baselines_endpoint(
    org_id: str = Query("default-org"),
    instrument: Optional[str] = Query(None, description="Filter by instrument type"),
    db: Session = Depends(get_db),
):
    """
    Get current baselines for an organization.

    Baselines are historical statistics (mean, std, sample count) used
    for drift detection. They are updated incrementally using Welford's
    algorithm when new data passes QC.

    Returns baselines grouped by instrument, then by field.
    """
    if instrument:
        # Get baselines for specific instrument
        baselines = get_baselines(org_id, instrument, db)
        return {
            "org_id": org_id,
            "instrument": instrument,
            "baselines": {
                field: {
                    "mean": data["mean"],
                    "std": data["std"],
                    "n_samples": data["n_samples"],
                    "last_updated": data["last_updated"].isoformat() if data["last_updated"] else None,
                }
                for field, data in baselines.items()
            },
        }
    else:
        # Get all baselines grouped by instrument
        all_baselines = get_all_baselines(org_id, db)
        return {
            "org_id": org_id,
            "instruments": all_baselines,
        }


@app.post("/api/v1/baselines/reset")
def reset_baselines_endpoint(
    req: BaselineResetRequest,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """
    Reset baselines for an instrument.

    Use this when:
    - Instrument is recalibrated
    - Method parameters change significantly
    - You want to start fresh with baseline collection

    If field_names is provided, only those fields are reset.
    Otherwise, all fields for the instrument are reset.
    """
    count = reset_baselines(
        org_id=org_id,
        instrument=req.instrument,
        field_names=req.field_names,
        db=db,
    )

    # Audit log
    log_audit(
        action=AuditAction.BASELINE_RESET,
        entity_type=EntityType.BASELINE,
        entity_id=f"{org_id}:{req.instrument}",
        actor="api",
        org_id=org_id,
        details={
            "instrument": req.instrument,
            "fields_reset": req.field_names or "all",
            "count": count,
        },
        db=db,
    )

    return {
        "status": "reset",
        "instrument": req.instrument,
        "fields_reset": req.field_names or "all",
        "baselines_deleted": count,
    }


@app.get("/api/v1/baselines/{instrument}/{field_name}")
def get_baseline_detail(
    instrument: str,
    field_name: str,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
):
    """
    Get detailed baseline for a specific instrument/field combination.

    Returns the full baseline record including internal state.
    """
    baseline = db.query(Baseline).filter(
        Baseline.org_id == org_id,
        Baseline.instrument == instrument,
        Baseline.field_name == field_name,
    ).first()

    if not baseline:
        raise HTTPException(
            status_code=404,
            detail=f"No baseline found for {instrument}/{field_name}"
        )

    return {
        "org_id": org_id,
        "instrument": instrument,
        "field_name": field_name,
        "mean": baseline.mean,
        "std": baseline.std,
        "n_samples": baseline.n_samples,
        "m2": baseline.m2,  # Internal Welford state
        "created_at": baseline.created_at.isoformat() if baseline.created_at else None,
        "last_updated": baseline.last_updated.isoformat() if baseline.last_updated else None,
    }


# --- Circuit Breaker Endpoints ---

@app.get(
    "/api/v1/circuit-breakers",
    tags=["System"],
    summary="Get circuit breaker status",
    description="""
Get the current status of all circuit breakers.

Circuit breakers protect against cascading failures when external services
(storage, webhooks, database) are experiencing issues.

States:
- **closed**: Normal operation
- **open**: Service is down, requests fail fast
- **half_open**: Testing if service recovered
""",
)
def get_circuit_breaker_status():
    """Get status of all circuit breakers."""
    return {
        "circuit_breakers": get_all_breaker_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    "/api/v1/circuit-breakers/reset",
    tags=["System"],
    summary="Reset all circuit breakers",
    description="""
Manually reset all circuit breakers to closed state.

Use this after:
- External service issues are resolved
- During maintenance or recovery procedures
- For testing purposes

**Note:** Only use this if you're confident the underlying issues are resolved.
""",
)
def reset_circuit_breakers():
    """Reset all circuit breakers to closed state."""
    reset_all_breakers()
    logger.info("All circuit breakers manually reset")
    return {
        "status": "reset",
        "circuit_breakers": get_all_breaker_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get(
    "/api/v1/circuit-breakers/{service}",
    tags=["System"],
    summary="Get specific circuit breaker status",
)
def get_circuit_breaker_by_service(service: str):
    """Get status of a specific circuit breaker."""
    breakers = {
        "storage": storage_breaker,
        "webhooks": webhook_breaker,
        "database": database_breaker,
    }

    if service not in breakers:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown circuit breaker: {service}. "
                   f"Available: {list(breakers.keys())}"
        )

    return {
        "circuit_breaker": breakers[service].get_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    "/api/v1/circuit-breakers/{service}/reset",
    tags=["System"],
    summary="Reset specific circuit breaker",
)
def reset_circuit_breaker_by_service(service: str):
    """Reset a specific circuit breaker."""
    breakers = {
        "storage": storage_breaker,
        "webhooks": webhook_breaker,
        "database": database_breaker,
    }

    if service not in breakers:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown circuit breaker: {service}. "
                   f"Available: {list(breakers.keys())}"
        )

    breakers[service].reset()
    logger.info(f"Circuit breaker '{service}' manually reset")

    return {
        "status": "reset",
        "circuit_breaker": breakers[service].get_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
