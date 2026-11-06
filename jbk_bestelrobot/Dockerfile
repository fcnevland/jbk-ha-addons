ARG BUILD_FROM
FROM ${BUILD_FROM}
ENV PYTHONUNBUFFERED=1 TZ=Europe/Amsterdam
RUN pip install --no-cache-dir aiohttp==3.10.11 \
 && apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY robot/ /app/
CMD ["python3", "-u", "/app/main.py"]
