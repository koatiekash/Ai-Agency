FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 PORT=8000 AGENCY_HOME=/var/agency

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home agency && mkdir -p /var/agency && chown -R agency /var/agency /app
USER agency

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')"
CMD ["python", "server.py"]
