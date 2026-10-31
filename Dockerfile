FROM python:3.13-slim

# libxrender1 libxext6 libexpat1: needed to import rdkit.Chem.Draw, missing from the slim image
RUN apt-get update && apt-get install -y --no-install-recommends curl libxrender1 libxext6 libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# no pytest cache: the container user cannot write to /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_ADDOPTS="-p no:cacheprovider"
WORKDIR /app
# all Python dependencies live in a virtual environment inside the image
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN python -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt

COPY VERSION .
COPY server/ server/
COPY tests/ tests/

EXPOSE 8910
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://localhost:8910/health || exit 1
CMD ["gunicorn", "--chdir", "server", "-b", "0.0.0.0:8910", "-w", "2", "--access-logfile", "-", "app:app"]
