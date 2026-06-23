# ROBOPORT control surface — live interactive demo
# Tiny image: the runtime feed needs only FastAPI + uvicorn (no docker-py).
# Build context is the REPO ROOT so it can include agents/registry.json next to
# control_surface/, matching the layout server.py expects.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29"

# Repo layout the server resolves at runtime: control_surface/ beside agents/.
COPY agents/registry.json /app/agents/registry.json
COPY control_surface /app/control_surface

WORKDIR /app/control_surface/collector
ENV ROBOPORT_FEED_SOURCE=runtime-demo \
    ROBOPORT_SERVE_WEB=1 \
    ROBOPORT_CREW=jd_crew \
    ROBOPORT_REGISTRY=/app/agents/registry.json \
    PORT=8000

EXPOSE 8000
CMD ["python", "server.py"]
