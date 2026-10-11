# Build from the repository root:  docker build -f services/notifier/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/notifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY services/notifier/*.py ./
RUN useradd -r -u 10001 appuser
USER 10001
EXPOSE 8001
CMD ["python", "notifier.py"]
