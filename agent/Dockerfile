# The agent server: Flask with sessions and an emulated agent; the page runs the TUI as Python in Pyodide, shown in restty.
# Built from the repository's root, to share the house stylesheet.

# Pyodide (Python 3.13 in WebAssembly), restty and the DejaVu fonts, each checked against its sha256
FROM alpine:3.20 AS vendor
RUN apk add --no-cache curl
RUN mkdir -p /vendor/pyodide && cd /tmp && set -e; \
    for spec in \
        "pyodide https://registry.npmjs.org/pyodide/-/pyodide-0.28.3.tgz bc3accb122c2c5d840b26ce56b100a21ab94aea4fe387a5b5c072ec6a88b7a62" \
        "restty https://registry.npmjs.org/restty/-/restty-0.3.0.tgz 7ffffb6b4d04e8344dd120effe93d081740b733e642e5f2bdae829eb3da16f37" \
        "dejavu https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz b7013ac58f9225250619db82cfb72e99c37ca8195f2b5a06a9e6a95bc5e1c84a"; do \
        set -- $spec; \
        curl -fsSL -o "$1.tgz" "$2"; \
        echo "$3  $1.tgz" | sha256sum -c -; \
        mkdir "$1" && tar -xzf "$1.tgz" -C "$1"; \
    done; \
    cd pyodide/package && cp pyodide.mjs pyodide.js pyodide.asm.js pyodide.asm.wasm python_stdlib.zip pyodide-lock.json /vendor/pyodide/ && cd /tmp; \
    printf 'Pyodide is MPL-2.0 licensed: https://github.com/pyodide/pyodide/blob/main/LICENSE\n' > /vendor/pyodide/LICENSE; \
    cp restty/package/dist/restty.esm.js /vendor/ && cp restty/package/LICENSE /vendor/LICENSE.restty; \
    cp dejavu/package/ttf/DejaVuSansMono.ttf dejavu/package/ttf/DejaVuSansMono-Bold.ttf /vendor/ && cp dejavu/package/LICENSE /vendor/LICENSE.dejavu; \
    rm -rf /tmp/*
COPY server/static/house.css /vendor/house.css

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_ADDOPTS="-p no:cacheprovider"
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH VENDOR_DIR=/opt/vendor
# the agents base class (sdk/tuiagent), and /app for agents named by AGENT, such as examples.hello_agent:HelloAgent
ENV PYTHONPATH=/app/sdk:/app
WORKDIR /app
COPY agent/requirements.txt .
RUN python -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt
COPY --from=vendor /vendor /opt/vendor
COPY agent/ /app/
EXPOSE 8950
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD curl -fs http://localhost:8950/health || exit 1
# one process, since sessions live in its memory; threads, since each open event stream holds one
CMD ["gunicorn", "--chdir", "server", "-b", "0.0.0.0:8950", "-w", "1", "-k", "gthread", "--threads", "64", "--timeout", "0", \
     "--access-logfile", "-", "app:app"]
