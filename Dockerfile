FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright + Chromium para el bot de radicación en la Rama Judicial.
# --with-deps instala las dependencias del sistema (libs de chromium).
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache/ms-playwright/*/.links

COPY . .

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p storage/tutelas storage/pruebas storage/constancias /data \
    && chown -R appuser:appuser /app /data /ms-playwright

USER appuser

EXPOSE 8000

CMD ["python", "run_server.py"]