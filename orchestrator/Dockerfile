# ============================================================
# Build stage — install dependencies into /deps
# ============================================================
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

# Install dependencies separately for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --target=/deps -r requirements.txt

COPY orchestrator.py .

# ============================================================
# Final runtime stage — minimal, non-root
# ============================================================
FROM python:3.11-slim-bookworm

# Security: run as a non-root user
RUN adduser --disabled-password --gecos "" appuser

WORKDIR /app

# Copy application and dependencies
COPY --from=builder /app /app
COPY --from=builder /deps /deps

ENV PYTHONPATH=/deps
ENV PATH="/deps/bin:$PATH"

# Python runtime hardening
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Pre-create directories with correct ownership so the non-root user can write
RUN mkdir -p /app/logs /app/cfg /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

# Healthcheck so Docker/Compose knows when the service is ready
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# Use Gunicorn with Uvicorn workers for production-grade concurrency.
# WEB_CONCURRENCY can be overridden at runtime (e.g. via docker-compose env).
CMD ["sh", "-c", \
     "gunicorn orchestrator:app \
      --worker-class uvicorn.workers.UvicornWorker \
      --workers ${WEB_CONCURRENCY:-2} \
      --bind 0.0.0.0:8000 \
      --timeout 120 \
      --graceful-timeout 10 \
      --access-logfile - \
      --error-logfile -"]
