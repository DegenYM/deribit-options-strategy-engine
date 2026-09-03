FROM python:3.12-slim

WORKDIR /app
COPY saas/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY saas/packages /app/packages
COPY saas/apps /app/apps
COPY saas/legal /app/legal
COPY saas/deploy /app/deploy

ENV PYTHONPATH=/app/packages/cc_engine:/app/apps/api:/app/apps/supervisor:/app/apps/marketd
ENV CC_SAAS_WEB_DIR=/app/apps/web
ENV CC_SAAS_DATA_DIR=/var/lib/cc-saas

EXPOSE 8080
CMD ["uvicorn", "cc_saas.main:app", "--host", "0.0.0.0", "--port", "8080"]
