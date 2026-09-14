# Hosted diary agent for pyunto-server (`pyunto-agent serve`).
# Build:  docker build -t pyunto-agent .
# Run:    see pyunto-server/docker-compose.agent.yml
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md persona.md ./
COPY pyunto_agent ./pyunto_agent
RUN pip install --no-cache-dir .
# identity.json / space_keys.json / device_id live here; mount a volume so they survive restarts.
ENV PYUNTO_AGENT_DIR=/data
VOLUME ["/data"]
EXPOSE 8788
CMD ["pyunto-agent", "serve", "--listen", "0.0.0.0:8788", "--persona", "/app/persona.md"]
