FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY simulator/orchestrator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY simulator/orchestrator/main.py           /app/main.py
COPY simulator/orchestrator/docker_manager.py /app/docker_manager.py
COPY simulator/orchestrator/fault_scenarios.py /app/fault_scenarios.py

ENV ORCHESTRATOR_PORT=8099

EXPOSE 8099

CMD ["python", "main.py"]
