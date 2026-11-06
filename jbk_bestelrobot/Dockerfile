# Kleine basis met alleen Chromium (ca. 700 MB in plaats van ruim 2 GB)
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 TZ=Europe/Amsterdam PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright==1.49.1 aiohttp==3.10.11 \
 && playwright install --with-deps chromium \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* /root/.cache
WORKDIR /app
COPY robot/ /app/
CMD ["python3", "-u", "/app/main.py"]
