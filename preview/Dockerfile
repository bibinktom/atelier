FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# LibreOffice + a TrueType font set. ~280 MB. This is the ONLY heavy parser
# in the deployment; isolating it here means a parser CVE doesn't reach the
# backend's secrets / DB / file mounts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Non-root, distinct from the tools sandbox uid (10001) so the two services
# can't accidentally collide on filesystem ownership if a volume is shared.
RUN useradd -u 10002 -m converter && chown -R converter /app
USER converter

EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
