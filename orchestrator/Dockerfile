
# --- Build Stage - Build dependencies separately --- # 
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

COPY main.py .

# --- Final Stage - Minimal runtime container --- #

# NOTE: Potential add in future?
# FROM gcr.io/distroless/python3

FROM python:3.11-slim-bookworm

# Create non-root user
RUN adduser --disabled-password appuser

WORKDIR /app

COPY --from=builder /app /app
COPY --from=builder /deps /deps

ENV PYTHONPATH=/deps
ENV PATH="/deps/bin:$PATH"

USER appuser

# Expose port for FastAPI
EXPOSE 8000

# Run app using Uvicorn
# NOTE: Maybe gunicorn???
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]