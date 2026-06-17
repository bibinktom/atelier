FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# fonts for reportlab/PDF rendering + toolchain for the coding agent
# (git: clone/patch real repos · ripgrep: codebase_search · node/npm + build-essential:
#  build & run real projects). Generated code runs under SAFE_ENV with no secrets and
#  the container keeps cap_drop:ALL + no-new-privileges (see docker-compose.yml).
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        git \
        ripgrep \
        patch \
        build-essential \
        nodejs \
        npm \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as non-root.
RUN useradd -u 10001 -m sandbox && chown -R sandbox /app
USER sandbox

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
