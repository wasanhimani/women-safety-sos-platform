FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY notifier.py .
RUN useradd -r -u 10001 appuser
USER 10001
EXPOSE 8001
CMD ["python", "notifier.py"]
