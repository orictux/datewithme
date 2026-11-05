# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    DB_PATH=/data/database.enc \
    DB_KEY_PATH=/secrets/database.key \
    SETUP_TOKEN_PATH=/secrets/setup.token \
    VAPID_PRIVATE_PATH=/secrets/vapid-private.pem

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && groupadd --gid 10001 datewithme \
    && useradd --uid 10001 --gid datewithme --no-create-home datewithme \
    && mkdir -p /data /secrets \
    && chown datewithme:datewithme /data /secrets \
    && chmod 700 /data /secrets
COPY --chown=datewithme:datewithme app/ /app/app/
COPY --chown=datewithme:datewithme web/ /app/web/
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
