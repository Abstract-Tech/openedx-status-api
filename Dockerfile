FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STATUS_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY openedx_status_api ./openedx_status_api
RUN pip install --no-cache-dir . \
 && useradd -u 10001 -m status && mkdir -p /data /config && chown status /data
USER 10001
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('STATUS_PORT', '8000'), timeout=3)" || exit 1
CMD ["python", "-m", "openedx_status_api"]
