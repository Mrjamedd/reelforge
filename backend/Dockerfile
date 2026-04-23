FROM python:3.11-slim

# Install system deps (ffmpeg for video processing, libmagic for file type detection)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libmagic1 \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create storage directory for local dev
RUN mkdir -p /storage/uploads /storage/thumbnails

EXPOSE 8000
