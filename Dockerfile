# JournalMind — OpenClaw enrichment agent container
#
# Builds on the official OpenClaw image. Bakes the JournalMind workspace and
# config into the image. At runtime only agent/output is mounted (read/write);
# the workspace and all tooling live inside the container.
#
# Build:
#   docker build -t journalmind-openclaw .
#
# The container is never run directly; run_agent.sh invokes it via docker run.

FROM ghcr.io/openclaw/openclaw:latest

# The base image runs as a non-root user; switch to root for system setup
USER root

# Install Chromium for browser-based web_fetch in Docker
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

# Bake the JournalMind workspace into the image.
# Only agent/output is mounted at runtime; the workspace must live here.
COPY agent/workspace /app/workspace

# Install the runtime entrypoint script.
# The entrypoint writes the full OpenClaw config at container start time
# (Ollama baseUrl, model entry, browser settings) so no baked config is needed.
COPY agent/docker/entrypoint.sh /usr/local/bin/openclaw-entrypoint.sh
RUN chmod +x /usr/local/bin/openclaw-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/openclaw-entrypoint.sh"]
