FROM python:3.11-slim

# Install system dependencies (ffmpeg required by pydub for audio conversion)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONPATH=/app/src

# Install Python dependencies first (separate layer for caching)
COPY requirements.txt requirements-common.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY templates/ templates/
COPY static/ static/

EXPOSE 8000

# Production: single worker
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
