# Geo-Manager: Python sidecar for HAProxy Geo-IP maps and staged rollout.
# Needs haproxy for config validation (-c) and socat for reload socket.
FROM python:3.11-alpine

# ca-certificates: für HTTPS-Downloads (z. B. Geo-CSV von raw.githubusercontent.com)
RUN apk add --no-cache ca-certificates haproxy socat
# Nach der Installation von ca-certificates hinzufügen
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_DIR=/etc/ssl/certs


WORKDIR /app
# When built from repo root: docker build -f geo-manager/Dockerfile .
COPY geo-manager/geo_manager/ ./geo_manager/

# So that "python main.py" finds the geo_manager package
ENV PYTHONPATH=/app
CMD ["python", "-m", "geo_manager"]
