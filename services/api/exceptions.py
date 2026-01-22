"""
Custom exceptions for LabLink AI.

Provides a hierarchy of exceptions for different error categories,
enabling structured error handling and consistent error responses.
"""

from typing import Optional, Dict, Any


class LabLinkError(Exception):
    """
    Base exception for all LabLink errors.

    All custom exceptions inherit from this class, allowing
    catch-all error handling when needed.
    """

    def __init__(
        self,
        message: str,
        code: str = "LABLINK_ERROR",
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        self.cause = cause
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON serialization."""
        result = {
            "error": self.code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        if self.cause:
            result["cause"] = str(self.cause)
        return result


# --- Parser Exceptions ---

class ParserError(LabLinkError):
    """
    Raised when a file cannot be parsed.

    This includes format detection failures, malformed files,
    and unsupported file types.
    """

    def __init__(
        self,
        message: str,
        filename: Optional[str] = None,
        parser: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if filename:
            details["filename"] = filename
        if parser:
            details["parser"] = parser

        super().__init__(
            message=message,
            code="PARSER_ERROR",
            details=details,
            cause=cause,
        )
        self.filename = filename
        self.parser = parser


class FormatDetectionError(ParserError):
    """Raised when file format cannot be detected."""

    def __init__(self, filename: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Could not detect format for file: {filename}",
            filename=filename,
            cause=cause,
        )
        self.code = "FORMAT_DETECTION_ERROR"


class UnsupportedFormatError(ParserError):
    """Raised when file format is not supported."""

    def __init__(self, filename: str, detected_format: Optional[str] = None):
        message = f"Unsupported file format: {filename}"
        if detected_format:
            message += f" (detected: {detected_format})"

        super().__init__(message=message, filename=filename)
        self.code = "UNSUPPORTED_FORMAT"
        self.detected_format = detected_format


# --- Schema Mapping Exceptions ---

class SchemaMatchError(LabLinkError):
    """
    Raised when schema mapping fails or has low confidence.
    """

    def __init__(
        self,
        message: str,
        headers: Optional[list] = None,
        confidence: Optional[float] = None,
        unmapped_fields: Optional[list] = None,
    ):
        details = {}
        if headers:
            details["headers"] = headers
        if confidence is not None:
            details["confidence"] = confidence
        if unmapped_fields:
            details["unmapped_fields"] = unmapped_fields

        super().__init__(
            message=message,
            code="SCHEMA_MATCH_ERROR",
            details=details,
        )
        self.headers = headers
        self.confidence = confidence
        self.unmapped_fields = unmapped_fields


class LowConfidenceMatchError(SchemaMatchError):
    """Raised when schema matching confidence is below threshold."""

    def __init__(
        self,
        headers: list,
        confidence: float,
        threshold: float = 0.5,
    ):
        super().__init__(
            message=f"Schema mapping confidence ({confidence:.2f}) below threshold ({threshold})",
            headers=headers,
            confidence=confidence,
        )
        self.code = "LOW_CONFIDENCE_MATCH"
        self.threshold = threshold


# --- Storage Exceptions ---

class StorageError(LabLinkError):
    """
    Raised for S3/MinIO storage issues.
    """

    def __init__(
        self,
        message: str,
        bucket: Optional[str] = None,
        key: Optional[str] = None,
        operation: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if bucket:
            details["bucket"] = bucket
        if key:
            details["key"] = key
        if operation:
            details["operation"] = operation

        super().__init__(
            message=message,
            code="STORAGE_ERROR",
            details=details,
            cause=cause,
        )
        self.bucket = bucket
        self.key = key
        self.operation = operation


class BucketNotFoundError(StorageError):
    """Raised when S3 bucket doesn't exist."""

    def __init__(self, bucket: str):
        super().__init__(
            message=f"Bucket not found: {bucket}",
            bucket=bucket,
            operation="head_bucket",
        )
        self.code = "BUCKET_NOT_FOUND"


class ObjectNotFoundError(StorageError):
    """Raised when S3 object doesn't exist."""

    def __init__(self, bucket: str, key: str):
        super().__init__(
            message=f"Object not found: {bucket}/{key}",
            bucket=bucket,
            key=key,
            operation="get_object",
        )
        self.code = "OBJECT_NOT_FOUND"


class StorageConnectionError(StorageError):
    """Raised when unable to connect to storage service."""

    def __init__(self, endpoint: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Cannot connect to storage: {endpoint}",
            cause=cause,
        )
        self.code = "STORAGE_CONNECTION_ERROR"
        self.endpoint = endpoint


# --- Validation Exceptions ---

class ValidationError(LabLinkError):
    """
    Raised for input validation failures.
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        constraint: Optional[str] = None,
    ):
        details = {}
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)[:100]  # Truncate long values
        if constraint:
            details["constraint"] = constraint

        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details=details,
        )
        self.field = field
        self.value = value
        self.constraint = constraint


class RequiredFieldError(ValidationError):
    """Raised when a required field is missing."""

    def __init__(self, field: str):
        super().__init__(
            message=f"Required field missing: {field}",
            field=field,
            constraint="required",
        )
        self.code = "REQUIRED_FIELD_MISSING"


class InvalidFormatError(ValidationError):
    """Raised when field value has invalid format."""

    def __init__(self, field: str, value: Any, expected_format: str):
        super().__init__(
            message=f"Invalid format for {field}: expected {expected_format}",
            field=field,
            value=value,
            constraint=f"format:{expected_format}",
        )
        self.code = "INVALID_FORMAT"
        self.expected_format = expected_format


# --- Webhook Exceptions ---

class WebhookError(LabLinkError):
    """
    Base exception for webhook-related errors.
    """

    def __init__(
        self,
        message: str,
        webhook_id: Optional[int] = None,
        url: Optional[str] = None,
        event_type: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if webhook_id:
            details["webhook_id"] = webhook_id
        if url:
            details["url"] = url
        if event_type:
            details["event_type"] = event_type

        super().__init__(
            message=message,
            code="WEBHOOK_ERROR",
            details=details,
            cause=cause,
        )
        self.webhook_id = webhook_id
        self.url = url
        self.event_type = event_type


class WebhookDeliveryError(WebhookError):
    """Raised when webhook delivery fails after all retries."""

    def __init__(
        self,
        url: str,
        webhook_id: Optional[int] = None,
        status_code: Optional[int] = None,
        attempts: int = 0,
        cause: Optional[Exception] = None,
    ):
        message = f"Webhook delivery failed to {url}"
        if status_code:
            message += f" (status: {status_code})"
        if attempts:
            message += f" after {attempts} attempts"

        super().__init__(
            message=message,
            webhook_id=webhook_id,
            url=url,
            cause=cause,
        )
        self.code = "WEBHOOK_DELIVERY_FAILED"
        self.status_code = status_code
        self.attempts = attempts


class WebhookTimeoutError(WebhookError):
    """Raised when webhook request times out."""

    def __init__(self, url: str, timeout_seconds: float):
        super().__init__(
            message=f"Webhook timed out after {timeout_seconds}s: {url}",
            url=url,
        )
        self.code = "WEBHOOK_TIMEOUT"
        self.timeout_seconds = timeout_seconds


# --- Database Exceptions ---

class DatabaseError(LabLinkError):
    """
    Raised for database operation failures.
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if operation:
            details["operation"] = operation
        if table:
            details["table"] = table

        super().__init__(
            message=message,
            code="DATABASE_ERROR",
            details=details,
            cause=cause,
        )
        self.operation = operation
        self.table = table


class ConnectionError(DatabaseError):
    """Raised when database connection fails."""

    def __init__(self, cause: Optional[Exception] = None):
        super().__init__(
            message="Failed to connect to database",
            operation="connect",
            cause=cause,
        )
        self.code = "DATABASE_CONNECTION_ERROR"


class RecordNotFoundError(DatabaseError):
    """Raised when a database record is not found."""

    def __init__(self, table: str, record_id: Any):
        super().__init__(
            message=f"Record not found: {table}[{record_id}]",
            operation="select",
            table=table,
        )
        self.code = "RECORD_NOT_FOUND"
        self.record_id = record_id


# --- QC Exceptions ---

class QCError(LabLinkError):
    """
    Raised for quality control failures.
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        check_type: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if field:
            details["field"] = field
        if check_type:
            details["check_type"] = check_type

        super().__init__(
            message=message,
            code="QC_ERROR",
            details=details,
            cause=cause,
        )
        self.field = field
        self.check_type = check_type


class QCFailureError(QCError):
    """Raised when QC fails with critical issues."""

    def __init__(self, summary: str, failed_fields: list):
        super().__init__(
            message=f"QC failed: {summary}",
        )
        self.code = "QC_FAILURE"
        self.failed_fields = failed_fields
        self.details["failed_fields"] = failed_fields


# --- Circuit Breaker Exceptions ---

class CircuitBreakerOpenError(LabLinkError):
    """Raised when circuit breaker is open and rejecting requests."""

    def __init__(self, service: str, reset_time: Optional[float] = None):
        message = f"Circuit breaker open for {service}"
        if reset_time:
            message += f", resets in {reset_time:.1f}s"

        super().__init__(
            message=message,
            code="CIRCUIT_BREAKER_OPEN",
            details={"service": service},
        )
        self.service = service
        self.reset_time = reset_time


# --- API Exceptions ---

class APIError(LabLinkError):
    """Raised for API communication errors."""

    def __init__(
        self,
        message: str,
        endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        cause: Optional[Exception] = None,
    ):
        details = {}
        if endpoint:
            details["endpoint"] = endpoint
        if status_code:
            details["status_code"] = status_code

        super().__init__(
            message=message,
            code="API_ERROR",
            details=details,
            cause=cause,
        )
        self.endpoint = endpoint
        self.status_code = status_code


class APIConnectionError(APIError):
    """Raised when unable to connect to API."""

    def __init__(self, endpoint: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Cannot connect to API: {endpoint}",
            endpoint=endpoint,
            cause=cause,
        )
        self.code = "API_CONNECTION_ERROR"


class APITimeoutError(APIError):
    """Raised when API request times out."""

    def __init__(self, endpoint: str, timeout: float):
        super().__init__(
            message=f"API request timed out after {timeout}s: {endpoint}",
            endpoint=endpoint,
        )
        self.code = "API_TIMEOUT"
        self.timeout = timeout
