FROM python:3.12.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps --no-build-isolation . && useradd --uid 10001 --create-home app
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
USER app
EXPOSE 8000
CMD ["uvicorn", "bi_backend.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
