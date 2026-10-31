FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION .
COPY server/ server/
COPY tests/ tests/

EXPOSE 8910
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://localhost:8910/health || exit 1
CMD ["gunicorn", "--chdir", "server", "-b", "0.0.0.0:8910", "-w", "2", "--access-logfile", "-", "app:app"]
