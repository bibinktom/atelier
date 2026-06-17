FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Document → PDF conversion is delegated to the sandboxed `preview` sidecar
# (see preview/Dockerfile). The backend never spawns LibreOffice; it just
# HTTP-POSTs file paths to http://preview:8002/convert.

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
