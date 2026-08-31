# QueryNest — backend image
# Minimal, single-stage build. Serves the FastAPI backend + single-file frontend.
# The frontend (querynest/api/static/index.html) is packaged inside the wheel,
# so no separate frontend build step is required.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUERYNEST_STORAGE_DIR=/data/querynest_storage \
    QUERYNEST_API_PORT=8080

WORKDIR /opt/querynest

# Install with core + API extras (skip heavy parsers / LLM dev deps in image)
COPY . .
RUN pip install --no-cache-dir ".[api]" && \
    rm -rf .git .github PHASE_9_TEST_REPORT tests testdata examples evaluation

# Runtime volumes for user documents / storage (not baked into the image)
VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["uvicorn", "querynest.api.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]