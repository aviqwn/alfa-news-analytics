FROM python:3.12.14-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app backend/ backend/
COPY --chown=app:app frontend/ frontend/
COPY --chown=app:app scripts/run.py scripts/run.py
COPY --chown=app:app data/synthetic/ data/synthetic/
RUN mkdir -p runtime data/processed data/raw && chown -R app:app runtime data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["python", "scripts/run.py", "--host", "0.0.0.0", "--port", "8000"]
