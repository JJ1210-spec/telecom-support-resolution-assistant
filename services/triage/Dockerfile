FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_ROOT=/app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
RUN useradd --create-home appuser && mkdir -p /app/.runtime && chown -R appuser /app
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8001/health')"
CMD ["uvicorn", "telecom_assistant.microservices.triage:app", "--host", "0.0.0.0", "--port", "8001"]
