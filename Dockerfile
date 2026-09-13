FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/var/lib/proxmox-ais \
    MASTER_KEY_FILE=/run/secrets/master.key

WORKDIR /app
RUN groupadd --gid 10001 provisioner \
    && useradd --uid 10001 --gid provisioner --no-create-home provisioner \
    && mkdir -p /var/lib/proxmox-ais /run/secrets \
    && chown -R provisioner:provisioner /var/lib/proxmox-ais /run/secrets
COPY pyproject.toml README.md ./
COPY provisioner ./provisioner
RUN pip install .

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3)"
ENTRYPOINT ["proxmox-ais"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
