# realmspace backend — the event bus, the graph, the consumers and the socket.
#
# Only the backend is containerised. The dashboard is a Node dev server and
# perception needs a webcam; neither belongs in a backend stack.

FROM python:3.12-slim

# Python in a container: don't write .pyc files into a layer that gets thrown
# away, and don't buffer stdout — buffered logs are invisible until a crash
# flushes them, which is exactly when you need them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl for the healthcheck below. --no-install-recommends keeps the layer small;
# cleaning the apt lists in the same RUN matters because a separate RUN would
# leave them in the previous layer regardless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Requirements before source, deliberately. Docker caches layers, so editing a
# .py file rebuilds from here down — reinstalling every dependency on every code
# change would make the edit loop unusable.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Run as a normal user. A container process running as root that is exposed to
# the network is a root process exposed to the network — the isolation is
# thinner than it looks.
RUN useradd --create-home --uid 10001 realmspace \
    && chown -R realmspace:realmspace /app
USER realmspace

EXPOSE 8000

# Compose uses this to decide the service is actually up, rather than merely
# started. `/health` reports both stores and the consumer tasks, so it is a real
# readiness signal and not just "the process exists".
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
