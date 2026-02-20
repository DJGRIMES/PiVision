FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN set -eux \
    && apt-get update \
    && apt-get install -y --no-install-recommends libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python3 -m compileall backend

EXPOSE 8080

CMD ["python3", "backend/server.py"]
