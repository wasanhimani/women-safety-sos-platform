# Build from the repository root:  docker build -f services/sos-api/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/sos-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY services/sos-api/*.py ./
RUN useradd -r -u 10001 appuser
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
