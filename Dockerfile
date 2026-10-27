FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Rulează ca utilizator neprivilegiat; /data e montat din docker-compose.
RUN useradd --create-home --uid 1000 tracker && mkdir -p /data && chown tracker /data
USER tracker

CMD ["hometracker", "loop"]
