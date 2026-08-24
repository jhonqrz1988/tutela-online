FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libexpat1 libxcb1 libxkbcommon0 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libatspi2.0-0 libwayland-client0 \
    libxcomposite1 libx11-xcb1 wget curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

COPY . .

RUN mkdir -p storage/tutelas storage/pruebas storage/constancias /data

# Volumen para persistencia en Railway: montar un Volume en /data y
# configurar DATABASE_URL=sqlite:////data/tutelas.db (ver .env.example).
VOLUME ["/data"]

EXPOSE 8000

CMD ["python", "run_server.py"]
