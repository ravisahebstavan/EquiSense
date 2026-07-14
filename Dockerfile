# EquiSense — works on Render, Hugging Face Spaces (Docker SDK), Fly, or any
# container host. HF Spaces expects port 7860; Render injects $PORT.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY equisense ./equisense
COPY web ./web
RUN pip install --no-cache-dir . "psycopg[binary]>=3.1"

# Ephemeral scratch dir for any file-mode fallbacks; real persistence is the
# DATABASE_URL Postgres (EQUISENSE_STORAGE=db is automatic for non-SQLite).
ENV EQUISENSE_DATA_DIR=/tmp/equisense-data
ENV PORT=7860
EXPOSE 7860

CMD uvicorn equisense.api.app:app --host 0.0.0.0 --port ${PORT}
