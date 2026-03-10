FROM python:3.11-alpine

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY cert-manager/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Nur das Python-Paket nach /app/cert_manager, damit "python -m cert_manager" das Modul findet
COPY cert-manager/cert_manager /app/cert_manager

CMD ["python", "-m", "cert_manager"]

