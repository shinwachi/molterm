# VisiData with the molterm plugin, and the demo that plays a recorded session of it in a web terminal.
# Built from the repository's root, to share the house stylesheet: docker build -f visidata-molterm/Dockerfile .
FROM python:3.13-slim

# terminfo the slim image lacks: kitty-terminfo for xterm-kitty, ncurses-term for ghostty and others
RUN apt-get update && apt-get install -y --no-install-recommends curl ncurses-term kitty-terminfo \
    && rm -rf /var/lib/apt/lists/*
# molterm-kitty: xterm-kitty with SGR mouse reports, for the live demo's web terminal, whose older reports break past column 95
RUN printf 'molterm-kitty|xterm-kitty with SGR mouse reports,\n\tuse=xterm+sm+1006, use=xterm-kitty,\n' > /tmp/molterm-kitty.ti \
    && tic -x -o /usr/share/terminfo /tmp/molterm-kitty.ti && rm /tmp/molterm-kitty.ti

# no pytest cache: the container user cannot write to /app. VisiData keeps its history in $HOME.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_ADDOPTS="-p no:cacheprovider" HOME=/tmp
WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
COPY visidata-molterm/requirements.txt .
RUN python -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt

# restty, a web terminal that shows Kitty graphics, and a font with Braille for it: the same as the main app's
RUN mkdir -p /opt/vendor && cd /tmp && set -e; \
    curl -fsSL -o restty.tgz https://registry.npmjs.org/restty/-/restty-0.3.0.tgz; \
    echo "7ffffb6b4d04e8344dd120effe93d081740b733e642e5f2bdae829eb3da16f37  restty.tgz" | sha256sum -c -; \
    tar -xzf restty.tgz; \
    cp package/dist/restty.esm.js /opt/vendor/; \
    cp package/LICENSE /opt/vendor/LICENSE.restty; \
    rm -rf package restty.tgz; \
    curl -fsSL -o dejavu.tgz https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz; \
    echo "b7013ac58f9225250619db82cfb72e99c37ca8195f2b5a06a9e6a95bc5e1c84a  dejavu.tgz" | sha256sum -c -; \
    tar -xzf dejavu.tgz; \
    cp package/ttf/DejaVuSansMono.ttf package/ttf/DejaVuSansMono-Bold.ttf /opt/vendor/; \
    cp package/LICENSE /opt/vendor/LICENSE.dejavu; \
    rm -rf package dejavu.tgz

COPY server/static/house.css /opt/vendor/house.css
COPY visidata-molterm/ /app/

EXPOSE 8920
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://localhost:8920/health || exit 1
CMD ["gunicorn", "--chdir", "demo", "-b", "0.0.0.0:8920", "-w", "1", "--threads", "4", "--access-logfile", "-", "app:app"]
