# LabLink AI API Documentation

## Overview

LabLink AI is a lab data middleware platform for biotechnology and life sciences.
It provides a unified data pipeline for laboratory instrument data with automated
schema mapping, quality control, and standardized output formats.

**Base URL:** `http://localhost:8000` (development)

**API Version:** 0.1.0

## Authentication

Currently, LabLink AI uses organization-based isolation via the `org_id` parameter.
Each organization's data is completely isolated.

```bash
# Include org_id in requests
curl -X GET "http://localhost:8000/api/v1/files?org_id=my-org"
```

> **Future:** OAuth2 and API key authentication will be added in a future release.

---

## Getting Started

### 1. Upload a File

First, get a presigned URL for upload:

```bash
curl -X POST "http://localhost:8000/api/v1/presign" \
  -H "Content-Type: application/json" \
  -d '{"filename": "sample_data.csv", "org_id": "my-org"}'
```

Response:
```json
{
  "url": "http://minio:9000/lablink-data",
  "fields": {
    "key": "data/my-org/sample_data.csv",
    "policy": "...",
    "signature": "..."
  }
}
```

Upload the file using the presigned URL:

```bash
curl -X POST "${url}" \
  -F "key=${fields.key}" \
  -F "policy=${fields.policy}" \
  -F "signature=${fields.signature}" \
  -F "file=@/path/to/sample_data.csv"
```

### 2. Submit the Manifest

After upload, submit the file manifest for processing:

```bash
curl -X POST "http://localhost:8000/api/v1/events" \
  -H "Content-Type: application/json" \
  -d '{
    "org_id": "my-org",
    "filename": "sample_data.csv",
    "s3_key": "data/my-org/sample_data.csv",
    "headers": ["Sample_ID", "Retention_Time", "Peak_Area"],
    "stats": {
      "Retention_Time": {
        "mean": 5.5,
        "std": 2.1,
        "min": 1.2,
        "max": 9.8,
        "values": [1.2, 3.4, 5.6, 7.8, 9.8]
      }
    }
  }'
```

### 3. Query Processed Files

```bash
curl "http://localhost:8000/api/v1/files?org_id=my-org"
```

---

## Core Endpoints

### File Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/presign` | Get presigned URL for file upload |
| POST | `/api/v1/events` | Submit file manifest for processing |
| GET | `/api/v1/files` | List processed files |
| GET | `/api/v1/files/{id}/normalized` | Get file in normalized format |

### Quality Control & Baselines

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/baselines` | List historical baselines |
| POST | `/api/v1/baselines/reset` | Reset baselines (after recalibration) |
| GET | `/api/v1/baselines/{instrument}/{field}` | Get specific baseline details |

### Data Transformation

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/formats` | List available output formats |
| GET | `/api/v1/files/{id}/normalized?format=lablink` | Transform to LabLink format |
| GET | `/api/v1/files/{id}/normalized?format=asm` | Transform to Allotrope format |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/webhooks` | List webhook subscriptions |
| POST | `/api/v1/webhooks` | Create webhook subscription |
| DELETE | `/api/v1/webhooks/{id}` | Delete webhook |
| POST | `/api/v1/webhooks/{id}/test` | Send test webhook |
| GET | `/api/v1/webhooks/events` | List available event types |

### Audit & Compliance

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/audit` | Query audit logs |
| GET | `/api/v1/audit/verify` | Verify audit chain integrity |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/healthz` | Simple health check (for k8s) |
| GET | `/api/v1/health` | Detailed health with service status |

---

## Webhook Integration

### Subscribing to Events

```bash
curl -X POST "http://localhost:8000/api/v1/webhooks" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-server.com/webhook",
    "events": ["file.processed", "qc.anomaly_detected"],
    "org_id": "my-org"
  }'
```

Response includes a `secret` for signature verification:
```json
{
  "id": 1,
  "url": "https://your-server.com/webhook",
  "events": ["file.processed", "qc.anomaly_detected"],
  "secret": "your-webhook-secret",
  "active": true
}
```

### Event Types

| Event | Description |
|-------|-------------|
| `file.ingested` | New file uploaded to storage |
| `file.processed` | All processing complete |
| `schema.mapped` | Schema mapping complete |
| `qc.completed` | QC analysis complete |
| `qc.anomaly_detected` | QC found anomalies |

### Webhook Payload

```json
{
  "event": "file.processed",
  "org_id": "my-org",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "file_id": "42",
    "filename": "sample_data.csv",
    "qc_status": "pass"
  }
}
```

### Signature Verification

Webhooks are signed with HMAC-SHA256. Verify signatures in your receiver:

**Headers:**
- `X-LabLink-Event`: Event type
- `X-LabLink-Signature`: `sha256=<signature>`
- `X-LabLink-Timestamp`: ISO8601 timestamp

**Python Example:**
```python
import hmac
import hashlib

def verify_signature(payload: str, signature: str, timestamp: str, secret: str) -> bool:
    """Verify webhook signature."""
    if signature.startswith("sha256="):
        signature = signature[7:]

    message = f"{timestamp}.{payload}"
    expected = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)
```

**Node.js Example:**
```javascript
const crypto = require('crypto');

function verifySignature(payload, signature, timestamp, secret) {
  if (signature.startsWith('sha256=')) {
    signature = signature.slice(7);
  }

  const message = `${timestamp}.${payload}`;
  const expected = crypto
    .createHmac('sha256', secret)
    .update(message)
    .digest('hex');

  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expected)
  );
}
```

### Retry Behavior

- **Attempts:** 3 retries with exponential backoff
- **Backoff:** 1s → 2s → 4s (max 30s)
- **Timeout:** 10 seconds per request
- **Auto-disable:** After 10 consecutive failures

---

## Supported Instruments

### Currently Supported

| Instrument | Parser | File Type |
|------------|--------|-----------|
| Agilent ChemStation | `agilent_chemstation` | `.D` folders |
| Generic CSV/TSV | `generic_csv` | `.csv`, `.tsv`, `.txt` |

### ChemStation .D Folders

The ChemStation parser handles Agilent's `.D` data folders:

```
sample.D/
├── acq.txt        # Acquisition parameters
├── RESULTS.CSV    # Peak integration results
├── DAD1A.ch       # Chromatogram data (binary)
└── sequence.acaml # Sequence info (optional)
```

Extracted metadata:
- Sample name, operator, method
- Injection datetime, vial, volume
- Instrument name, software version
- Peak table (retention time, area, height, compound)

---

## Schema Mapping

### How It Works

LabLink AI uses sentence-transformer embeddings to semantically match column
headers to canonical field names.

1. Headers are embedded using `all-MiniLM-L6-v2`
2. Cosine similarity is computed against canonical fields
3. Best match above 0.65 threshold is selected
4. Confidence score indicates match quality

### Canonical Fields

Common fields in the ontology:

| Category | Fields |
|----------|--------|
| Identifiers | `sample_id`, `batch_id`, `experiment_id` |
| Chromatography | `retention_time`, `peak_area`, `peak_height` |
| Concentration | `concentration`, `dilution_factor` |
| Quality | `purity`, `absorbance`, `optical_density` |
| Metadata | `operator`, `instrument_id`, `method_name` |

### Mapping Response

```json
{
  "mapping": {
    "Ret. Time": "retention_time",
    "Area": "peak_area",
    "Sample ID": "sample_id",
    "Unknown_Column": "unknown"
  },
  "confidence": {
    "Ret. Time": 0.92,
    "Area": 0.87,
    "Sample ID": 0.95,
    "Unknown_Column": 0.0
  }
}
```

---

## Quality Control Flags

### QC Check Types

| Type | Description | Severity |
|------|-------------|----------|
| `zscore` | Value deviates >3σ from mean | warn |
| `drift` | Batch mean differs from historical | fail |
| `monotonicity` | Unexpected direction change | warn |
| `discontinuity` | Jump >3x typical step size | warn |
| `range` | Value outside expected range | warn/fail |
| `missing` | High null percentage (>10%) | fail |

### QC Response Structure

```json
{
  "overall_status": "warn",
  "summary": "QC warnings detected. Issues: Retention_Time: zscore",
  "qc_flags": {
    "Retention_Time": {
      "status": "warn",
      "stats": {
        "mean": 5.5,
        "std": 2.1,
        "n": 20,
        "null_pct": 0.0
      },
      "anomalies": [
        {
          "type": "zscore",
          "details": {
            "index": 15,
            "value": 15.2,
            "zscore": 4.6,
            "threshold": 3.0
          }
        }
      ]
    }
  }
}
```

### Status Levels

| Status | Meaning |
|--------|---------|
| `pass` | All checks passed |
| `warn` | Issues detected but data usable |
| `fail` | Critical issues, review required |

---

## Historical Baselines

### Purpose

Baselines track historical statistics per instrument/field combination to enable
drift detection. They're updated incrementally using Welford's algorithm.

### Drift Detection Flow

1. New file uploaded
2. Current batch mean computed
3. Compared to historical baseline (z-test)
4. If difference > 2σ → drift anomaly flagged
5. If QC passes → baseline updated with new data
6. If QC fails → baseline NOT updated (prevent corruption)

### Resetting Baselines

After instrument recalibration or method changes:

```bash
curl -X POST "http://localhost:8000/api/v1/baselines/reset" \
  -H "Content-Type: application/json" \
  -d '{
    "instrument": "agilent_chemstation",
    "field_names": null
  }'
```

---

## Output Formats

### LabLink Standard Format (LSF)

Our canonical JSON schema for normalized data:

```json
{
  "version": "1.0",
  "source": {
    "instrument": "agilent_chemstation",
    "filename": "sample.D",
    "ingested_at": "2024-01-15T10:30:00Z",
    "org_id": "my-org"
  },
  "sample": {
    "id": "SAMPLE001",
    "name": "Caffeine Standard",
    "metadata": {}
  },
  "measurements": [
    {
      "field": "retention_time",
      "original_field": "Ret. Time",
      "value": 4.125,
      "unit": "min"
    }
  ],
  "qc": {
    "status": "pass",
    "flags": []
  },
  "lineage": {
    "raw_s3_key": "data/my-org/sample.D",
    "processing_version": "1.0.0",
    "schema_mapping_confidence": 0.87,
    "checksum": "abc123..."
  }
}
```

### Allotrope Simple Model (ASM)

Partial implementation of the Allotrope Foundation's standard:

```json
{
  "@context": {
    "asm": "https://www.allotrope.org/asm/",
    "qudt": "http://qudt.org/schema/qudt/"
  },
  "@type": "asm:AnalyticalDataDocument",
  "asm:technique": "asm:LiquidChromatography",
  "asm:sampleDocument": {
    "asm:sampleIdentifier": "SAMPLE001"
  },
  "asm:dataElement": [
    {
      "asm:fieldName": "retention_time",
      "asm:statisticalAggregate": {
        "asm:mean": {"qudt:numericValue": 4.125, "qudt:unit": "unit:MIN"}
      }
    }
  ]
}
```

---

## Request Tracing

All requests are assigned a unique ID for tracing:

- **Header:** `X-Request-ID`
- **Auto-generated** if not provided
- **Included** in response headers
- **Logged** with all operations

```bash
# Provide your own request ID
curl -H "X-Request-ID: my-trace-123" http://localhost:8000/api/v1/health

# Response header
# X-Request-ID: my-trace-123
```

---

## Error Handling

### Error Response Format

```json
{
  "detail": "File not found",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad request (invalid input) |
| 404 | Resource not found |
| 500 | Internal server error |

---

## Rate Limits

Currently no rate limits are enforced. Future versions may add:

- Per-organization rate limits
- Burst limits for webhook delivery
- Upload size limits

---

## Interactive Documentation

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`
