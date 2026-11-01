FROM python:3.12.14-slim-trixie AS deps
# base fixada (build reproduzível) + patches de segurança do Debian aplicados no build (scan: Trivy)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && useradd --system --uid 10001 app

FROM deps AS base
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "aicommerce.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Robô de publicação: Playwright/Chromium numa camada própria (mudança de código não reinstala o navegador).
FROM deps AS robot
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv sync --frozen --no-dev --no-install-project --extra robot \
    && /app/.venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /ms-playwright
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --extra robot
ENV PATH="/app/.venv/bin:$PATH"
USER app
CMD ["python", "-m", "aicommerce.robot"]
