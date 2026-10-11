# Meridian Clinical Exchange, the mock data feed. Build from the repository root:
#   docker build -f feed/Dockerfile -t meridian .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FEED_DATA_DIR=/data

WORKDIR /app

COPY packages/clinsim /opt/clinsim
RUN pip install --no-cache-dir /opt/clinsim

COPY feed/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY feed/meridian ./meridian

VOLUME /data
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8100/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "meridian.main:app", "--host", "0.0.0.0", "--port", "8100"]
