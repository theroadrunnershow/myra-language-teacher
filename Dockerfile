FROM python:3.11-slim

# Install system dependencies (ffmpeg required by pydub for audio conversion)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (separate layer for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py words_db.py speech_service.py tts_service.py ./
COPY templates/ templates/
COPY static/ static/

# Pre-download faster-whisper base model (~140 MB CTranslate2 format) so first request isn't slow
# base offers meaningfully better accuracy on Telugu/Assamese vs tiny (~39 MB) at modest CPU cost
# Model saved to /root/.cache/huggingface/hub/ inside the image
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('faster-whisper base model cached.')"

EXPOSE 8000

# Production: single worker (Whisper model stays in memory, no reload)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
