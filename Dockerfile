FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Bucharest \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependențele întâi, ca stratul să rămână în cache când se schimbă doar codul.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# Rulează ca utilizator neprivilegiat; /data e montat din compose.yaml.
RUN useradd --create-home --uid 1000 tracker && mkdir -p /data && chown tracker /data
USER tracker

CMD ["hometracker", "loop"]
