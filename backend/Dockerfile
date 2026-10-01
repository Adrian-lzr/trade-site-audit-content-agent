FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.lock backend/pyproject.toml /app/backend/
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.lock

COPY backend /app/backend
COPY demo-site /app/demo-site

# Keep the API, worker, and demo fixture from running as root.  The UID is
# stable so bind mounts and generated files have predictable ownership when a
# local developer chooses to add one.
RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /home/app app \
    && mkdir -p /home/app \
    && chown -R app:app /app /home/app

USER app

EXPOSE 8000 8765

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
