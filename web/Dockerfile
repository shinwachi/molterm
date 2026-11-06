# molterm in the browser: static files only. nginx serves them; RDKit runs in the visitor's browser as WebAssembly.
# Built from the repository's root, to share the house stylesheet.

# RDKit.js (the same RDKit as the server), restty and the DejaVu fonts, each checked against its sha256
FROM alpine:3.20 AS vendor
RUN apk add --no-cache curl
RUN mkdir -p /vendor && cd /tmp && set -e; \
    for spec in \
        "rdkit https://registry.npmjs.org/@rdkit/rdkit/-/rdkit-2026.9.1.tgz ae6b0e42b85114a2f38ef890fcb229a90b54b0b42d4636c50ae1c7f77d85ffe0" \
        "restty https://registry.npmjs.org/restty/-/restty-0.3.0.tgz 7ffffb6b4d04e8344dd120effe93d081740b733e642e5f2bdae829eb3da16f37" \
        "dejavu https://registry.npmjs.org/dejavu-fonts-ttf/-/dejavu-fonts-ttf-2.37.3.tgz b7013ac58f9225250619db82cfb72e99c37ca8195f2b5a06a9e6a95bc5e1c84a"; do \
        set -- $spec; \
        curl -fsSL -o "$1.tgz" "$2"; \
        echo "$3  $1.tgz" | sha256sum -c -; \
        mkdir "$1" && tar -xzf "$1.tgz" -C "$1"; \
    done; \
    cp rdkit/package/dist/RDKit_minimal.js rdkit/package/dist/RDKit_minimal.wasm /vendor/; \
    cp restty/package/dist/restty.esm.js /vendor/ && cp restty/package/LICENSE /vendor/LICENSE.restty; \
    cp dejavu/package/ttf/DejaVuSansMono.ttf dejavu/package/ttf/DejaVuSansMono-Bold.ttf /vendor/ && cp dejavu/package/LICENSE /vendor/LICENSE.dejavu; \
    printf 'RDKit is BSD-3-Clause licensed: https://github.com/rdkit/rdkit/blob/master/license.txt\n' > /vendor/LICENSE.rdkit; \
    rm -rf /tmp/*
COPY server/static/house.css /vendor/house.css

# the tests: Node with RDKit.js, the site and tests bind-mounted or copied
FROM node:22-alpine AS test
WORKDIR /web
COPY --from=vendor /vendor /web/vendor
COPY web/site /web/site
COPY web/tests /web/tests
CMD ["node", "--test", "tests/molterm.test.mjs"]

FROM nginx:1.27-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=vendor /vendor /srv/vendor
COPY web/site /srv/site
EXPOSE 8940
# the page and RDKit's WebAssembly both served: the page is no use without either
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8940/ && wget -q --spider http://127.0.0.1:8940/vendor/RDKit_minimal.wasm || exit 1
