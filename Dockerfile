FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml ./
COPY src ./src
COPY "List.docx" ./

RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

CMD ["python", "-m", "rodcom_bot.main"]
