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

# xterm.js, for the web terminal that shows real graphics sequences. Outside server/, which dev bind-mounts over.
RUN mkdir -p /app/vendor && cd /tmp && set -e; \
    for spec in \
        "xterm 5.5.0 bd954fa721872170188cc5d7e83e88db3c83c9a18a4e8d24c2783d26491f59d2" \
        "addon-image 0.8.0 002f7492884300c0ed691bbe7381263d066b6b074d531b131466d8c774565fc0" \
        "addon-fit 0.10.0 917ac44972453d5eed52edc1e50260c76398ce48cf2290c2e60671102bba0b33"; do \
        set -- $spec; \
        curl -fsSL -o "$1.tgz" "https://registry.npmjs.org/@xterm/$1/-/$1-$2.tgz"; \
        echo "$3  $1.tgz" | sha256sum -c -; \
        tar -xzf "$1.tgz"; \
        cp package/lib/*.js /app/vendor/; \
        cp package/LICENSE "/app/vendor/LICENSE.$1"; \
        if [ -d package/css ]; then cp package/css/*.css /app/vendor/; fi; \
        rm -rf package "$1.tgz"; \
    done

COPY VERSION .
COPY server/ server/
COPY tests/ tests/

EXPOSE 8910
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://localhost:8910/health || exit 1
CMD ["gunicorn", "--chdir", "server", "-b", "0.0.0.0:8910", "-w", "2", "--access-logfile", "-", "app:app"]
