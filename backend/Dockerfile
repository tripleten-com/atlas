# Atlas API and ingestion worker. Build from the repository root:
#   docker build -f backend/Dockerfile -t atlas-api:sha-$(git rev-parse --short HEAD) .
#
# One image, three commands:
#   uvicorn app.main:app --host 0.0.0.0 --port 8000   the API (the default below)
#   python -m app.ingest.worker                        the ingestion worker
#   python -m app.seed                                 migrate and seed (Compose runs it before
#                                                      the API; on Kubernetes it is a Job)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# the shared clinical simulator (reference data and the demo seed use it)
COPY packages/clinsim /opt/clinsim
RUN pip install --no-cache-dir /opt/clinsim

COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY backend/ ./

# trained model files are baked into the image so the API can serve them without a volume
# (the directory may hold only .gitkeep; Compose mounts ./models over it for local work)
COPY models/ /app/models/

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# serve. Migrations and the seed run separately (see the top of this file).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
