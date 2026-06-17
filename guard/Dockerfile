# Multi-stage build (phase 3): the builder installs the toolchain, downloads the
# spaCy tokenizer, and bakes all model weights; the runtime stage copies only the
# installed packages + baked weights, dropping pip/apt metadata, build temp,
# bytecode caches and package test trees. Same offline, read-only, non-root
# profile as before — just a smaller final image.

# ---------- builder ----------
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

# Only TLS certs are needed at build time (model weights are fetched from HF).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --extra-index-url makes the CPU torch wheel (torch==2.4.1+cpu) resolvable.
RUN pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY app ./app

# spaCy tokenizer model for Presidio (the PII Sensitive scanner's NLP engine).
RUN python -m spacy download en_core_web_sm

# Bake all model weights (DeBERTa prompt-injection, toxicity, PII NER) into /opt/hf
# during the build — the ONLY point egress is allowed.
RUN python -c "import app.scanners as s; s.warm()"

# Importing codeshield creates a semgrep-core -> osemgrep symlink in site-packages.
# Do it now (writable fs) so the read-only runtime rootfs doesn't trip on it. The
# symlink lives inside site-packages, so it carries over to the runtime stage.
RUN python -c "import codeshield.cs; print('[guard] codeshield import OK at build')"

# Drop build-only bloat that needn't ship: bytecode caches and package test trees.
# (Model weights in /opt/hf and the libraries themselves stay.)
RUN find /usr/local/lib/python3.12/site-packages -depth -type d -name '__pycache__' -exec rm -rf {} + \
 && find /usr/local/lib/python3.12/site-packages -depth -type d -name 'tests' -exec rm -rf {} +

# ---------- runtime ----------
FROM python:3.12-slim

WORKDIR /app

# Force offline at runtime so a missing-cache bug can't trigger a silent fetch.
# Semgrep needs writable config/cache + metrics off (no network); point it at /tmp
# (tmpfs) since the rootfs is read-only.
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1 \
    XDG_CONFIG_HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    SEMGREP_SETTINGS_FILE=/tmp/semgrep_settings.yml \
    SEMGREP_SEND_METRICS=off

# Copy only runtime artifacts: installed packages, console scripts, baked weights,
# and the app. No apt/pip metadata or build temp comes along.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /opt/hf /opt/hf
COPY --from=builder /app/app /app/app

# Non-root, matches the hardened sidecar profile (preview/tools).
RUN useradd -u 10003 -m guard && chown -R guard /app /opt/hf
USER guard

EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
