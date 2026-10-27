# Streamlit image for local runs and Render. Listens on $PORT (default 7860).
# Streamlit Community Cloud does not use this file; see docs/DEPLOY.md.
# No application entrypoint is in this tree yet. Install only pinned requirements.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    STREAMLIT_APP=

WORKDIR /app

RUN adduser --disabled-password --gecos "" --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/streamlit-entrypoint.sh /usr/local/bin/streamlit-entrypoint
RUN chmod 755 /usr/local/bin/streamlit-entrypoint

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '7860') + '/_stcore/health', timeout=4)"

ENTRYPOINT ["streamlit-entrypoint"]
