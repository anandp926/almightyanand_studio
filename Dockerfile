# One image for the orchestrator and both gateway services.
# Dependencies are baked in at build time instead of being pip-installed on every boot.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-liberation tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source is bind-mounted by docker-compose so code changes do not need a rebuild;
# it is also copied in so the image runs on its own.
COPY agents ./agents
COPY services ./services
COPY infra ./infra
COPY configs ./configs
COPY skills ./skills
COPY assets ./assets

CMD ["python", "-m", "agents.orchestrator", "--daemon"]
