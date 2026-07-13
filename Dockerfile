FROM python:3.11-slim

WORKDIR /app

# System deps for trafilatura and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Persistent SQLite volume
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-u", "bot.py"]
