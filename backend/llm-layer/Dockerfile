FROM python:3.13-slim AS build

ENV POETRY_VERSION=1.8.5 \
    POETRY_NO_INTERACTION=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

WORKDIR /app

COPY pyproject.toml ./
RUN poetry export --only main --format requirements.txt --without-hashes --output requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY README.md ./

EXPOSE 8010

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
