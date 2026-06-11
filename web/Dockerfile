FROM python:3.12-slim

WORKDIR /app

# Install deps first so they cache across code changes.
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dashboard app...
COPY web/ .
# ...plus the shared modules it reuses for the NOAA "Update" sync (build context = repo root).
COPY db.py reader.py backfill_noaa.py ./

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
