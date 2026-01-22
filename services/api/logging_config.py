"""
Structured logging configuration for LabLink AI.

Provides JSON-formatted logs with consistent context fields
for tracing and debugging in production environments.
"""

import logging
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextvars import ContextVar

try:
    from pythonjsonlogger import jsonlogger
    HAS_JSON_LOGGER = True
except ImportError:
    HAS_JSON_LOGGER = False

# Context variables for request-scoped data
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
org_id_ctx: ContextVar[str] = ContextVar("org_id", default="")
file_id_ctx: ContextVar[str] = ContextVar("file_id", default="")


class LabLinkJsonFormatter(jsonlogger.JsonFormatter if HAS_JSON_LOGGER else logging.Formatter):
    """
    Custom JSON formatter that includes LabLink context fields.

    Adds:
    - timestamp (ISO8601)
    - request_id
    - org_id
    - file_id (when available)
    - service name
    """

    def __init__(self, *args, service_name: str = "lablink-api", **kwargs):
        self.service_name = service_name
        if HAS_JSON_LOGGER:
            super().__init__(*args, **kwargs)
        else:
            super().__init__()

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict):
        """Add custom fields to log record."""
        if HAS_JSON_LOGGER:
            super().add_fields(log_record, record, message_dict)

        # Standard fields
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["service"] = self.service_name

        # Context fields
        request_id = request_id_ctx.get()
        if request_id:
            log_record["request_id"] = request_id

        org_id = org_id_ctx.get()
        if org_id:
            log_record["org_id"] = org_id

        file_id = file_id_ctx.get()
        if file_id:
            log_record["file_id"] = file_id

        # Source location
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno

        # Exception info
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        if HAS_JSON_LOGGER:
            return super().format(record)

        # Fallback to simple format if json logger not available
        request_id = request_id_ctx.get()
        org_id = org_id_ctx.get()
        file_id = file_id_ctx.get()

        context_parts = []
        if request_id:
            context_parts.append(f"req={request_id[:8]}")
        if org_id:
            context_parts.append(f"org={org_id}")
        if file_id:
            context_parts.append(f"file={file_id}")

        context = " ".join(context_parts)
        if context:
            context = f"[{context}] "

        return f"{datetime.now(timezone.utc).isoformat()} {record.levelname} {context}{record.getMessage()}"


def setup_logging(
    level: str = "INFO",
    service_name: str = "lablink-api",
    json_output: bool = True,
) -> logging.Logger:
    """
    Configure structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        service_name: Service name to include in logs
        json_output: Whether to use JSON format (requires python-json-logger)

    Returns:
        Configured root logger
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))

    # Set formatter
    if json_output and HAS_JSON_LOGGER:
        formatter = LabLinkJsonFormatter(service_name=service_name)
    else:
        formatter = LabLinkJsonFormatter(service_name=service_name)

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """
    Context manager for setting logging context.

    Usage:
        with LogContext(request_id="abc123", org_id="acme"):
            logger.info("Processing request")
    """

    def __init__(
        self,
        request_id: Optional[str] = None,
        org_id: Optional[str] = None,
        file_id: Optional[str] = None,
    ):
        self.request_id = request_id
        self.org_id = org_id
        self.file_id = file_id
        self._tokens = []

    def __enter__(self):
        if self.request_id:
            self._tokens.append(request_id_ctx.set(self.request_id))
        if self.org_id:
            self._tokens.append(org_id_ctx.set(self.org_id))
        if self.file_id:
            self._tokens.append(file_id_ctx.set(self.file_id))
        return self

    def __exit__(self, *args):
        for token in reversed(self._tokens):
            try:
                token.var.reset(token)
            except ValueError:
                pass


def log_operation(
    logger: logging.Logger,
    operation: str,
    level: int = logging.INFO,
    **context
) -> Dict[str, Any]:
    """
    Log an operation with structured context.

    Args:
        logger: Logger instance
        operation: Operation name
        level: Log level
        **context: Additional context fields

    Returns:
        Context dict for inclusion in exception handling
    """
    message = f"Operation: {operation}"
    extra = {"operation": operation, **context}

    logger.log(level, message, extra=extra)

    return extra


def log_exception(
    logger: logging.Logger,
    error: Exception,
    operation: Optional[str] = None,
    **context
):
    """
    Log an exception with full context.

    Args:
        logger: Logger instance
        error: The exception
        operation: Optional operation name
        **context: Additional context
    """
    extra = {"error_type": type(error).__name__, **context}
    if operation:
        extra["operation"] = operation

    # Include exception details if it's a LabLink exception
    if hasattr(error, "to_dict"):
        extra["error_details"] = error.to_dict()

    logger.exception(f"Error in {operation or 'operation'}: {error}", extra=extra)
