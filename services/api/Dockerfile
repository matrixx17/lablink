# ============================================
# LabLink AI API - Production Dockerfile
# ============================================
# Multi-stage build for smaller, secure images
# Supports both development and production modes

# --------------------------------------------
# Stage 1: Builder
# --------------------------------------------
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --------------------------------------------
# Stage 2: Runtime
# --------------------------------------------
FROM python:3.11-slim as runtime

# Labels for container metadata
LABEL maintainer="LabLink AI Team"
LABEL version="0.1.0"
LABEL description="LabLink AI API - Lab Data Middleware Platform"

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd --gid 1000 lablink && \
    useradd --uid 1000 --gid lablink --shell /bin/bash --create-home lablink

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=lablink:lablink . .

# Create directories for runtime
RUN mkdir -p /app/logs /app/data && \
    chown -R lablink:lablink /app

# Switch to non-root user
USER lablink

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    # Uvicorn settings
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    UVICORN_WORKERS=1 \
    # Logging
    LOG_LEVEL=INFO \
    JSON_LOGS=true

# Expose port
EXPOSE 8000

# Health check - checks the /healthz endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command - production mode with gunicorn
# Override with docker-compose for development
CMD ["python", "-m", "uvicorn", "app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
