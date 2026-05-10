FROM python:3.11-slim

# Required for ip alias management (ip addr add)
RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY simulator/network-sim/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy existing Datacenter_Network_Simulator codebase
COPY Datacenter_Network_Simulator/ /app/Datacenter_Network_Simulator/

# Copy new bridge/entrypoint files
COPY simulator/network-sim/entrypoint.py      /app/entrypoint.py
COPY simulator/network-sim/http_api.py        /app/http_api.py
COPY simulator/network-sim/dcim_bridge.py     /app/dcim_bridge.py
COPY simulator/network-sim/headless_trap_engine.py /app/headless_trap_engine.py
COPY simulator/network-sim/ip_alias_manager.py /app/ip_alias_manager.py

# Config files
COPY simulator/network-sim/config/ /app/config/

# Ensure Datacenter_Network_Simulator is importable
ENV PYTHONPATH=/app:/app/Datacenter_Network_Simulator

# Dataset directories (will be written at runtime)
RUN mkdir -p /app/datasets/snmp /app/datasets/gnmi

# Default environment
ENV CONFIG_FILE=/app/config/network-a.yaml
ENV DATASETS_DIR=/app/datasets
ENV TOPOLOGIES_DIR=/app/Datacenter_Network_Simulator/topologies
ENV BIND_INTERFACE=lo
ENV API_PORT=8090
ENV DCIM_SERVER_URL=http://dcim-server:8080/api/v1
ENV SFLOW_COLLECTOR_IP=aggregator
ENV TRAP_RECEIVER_IP=aggregator

EXPOSE 161/udp
EXPOSE 50051
EXPOSE 8090

CMD ["python", "entrypoint.py"]
