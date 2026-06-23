# ROBOPORT Ops Console — live demo image
# Runs a real jd_crew run (Anthropic) and serves the Ops Console + SSE bridge.
# Build context is the REPO ROOT (needs scripts/, agents/, resources/, config/).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Repo bits the runtime + bridge need (kept lean — no runs/, .git, evals output)
COPY scripts   /app/scripts
COPY agents    /app/agents
COPY resources /app/resources
COPY config    /app/config
COPY evals     /app/evals
COPY dashboard /app/dashboard

ENV PYTHONUNBUFFERED=1 \
    ROBOPORT_PROVIDER=anthropic \
    ROBOPORT_RUNS=1

EXPOSE 4242
ENTRYPOINT ["/app/dashboard/docker-entrypoint.sh"]
