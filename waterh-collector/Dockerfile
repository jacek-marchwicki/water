ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:latest
FROM ${BUILD_FROM}

# Install system dependencies (Python, BlueZ, jq, tzdata, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    bluez \
    jq \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip3 install --no-cache-dir --break-system-packages \
    bleak \
    paho-mqtt

# Set workdir and copy code
WORKDIR /app
COPY collector/ /app/collector/
COPY frontend/ /app/frontend/
COPY run.sh /app/run.sh

RUN chmod +x /app/run.sh

CMD [ "/app/run.sh" ]

