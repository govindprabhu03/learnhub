FROM python:3.12-slim-bookworm

# OpenCV (pulled in by the OCR engine) needs these two shared libraries, which slim images do not ship
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 LEARNHUB_EMBED_MODEL=$EMBED_MODEL LEARNHUB_MODEL_DIR=/opt/models
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# bake the embedding model into the image so containers start without downloading anything
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('$EMBED_MODEL', cache_dir='/opt/models')"

COPY alembic.ini .
COPY alembic ./alembic
COPY backend ./backend
COPY frontend ./frontend

# run as a non-root user; /app/data holds uploads (and the SQLite file / signing secret when not using SECRET_KEY / Postgres)
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app /app/data /opt/models
USER app
VOLUME /app/data
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import os, urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:%s/api/health' % os.environ.get('PORT', '8100'), timeout=4).status == 200 else 1)"

# hosts such as Render tell the container which port to bind through $PORT; default 8100 for local/compose use
CMD ["sh", "-c", "exec uvicorn main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8100}"]
