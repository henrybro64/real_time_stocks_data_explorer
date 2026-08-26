FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ACCEPT_EULA=Y

WORKDIR /app/backend

# Microsoft ODBC Driver 18 for Azure SQL (mssql+pyodbc://RANKINGS_ARCHIVE_DATABASE_URL).
# Apt signing key is vendored under backend/docker/ (no curl|gpg pipe).
# Leave RANKINGS_ARCHIVE_DATABASE_URL unset and the app still runs without Azure.
COPY backend/docker/microsoft.asc /tmp/microsoft.asc
COPY backend/docker/mssql-release.list /etc/apt/sources.list.d/mssql-release.list
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gnupg \
    && gpg --batch --dearmor -o /usr/share/keyrings/microsoft-prod.gpg /tmp/microsoft.asc \
    && rm -f /tmp/microsoft.asc \
    && apt-get update \
    && apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt

# Exact pins live in requirements.txt; install explicitly so Sonar does not treat -r as unlocked.
RUN python -m pip install "pip==25.2" \
    && python -m pip install --only-binary=:all: \
        "fastapi==0.116.1" \
        "uvicorn[standard]==0.35.0" \
        "pydantic==2.11.7" \
        "pydantic-settings==2.10.1" \
        "sqlalchemy==2.0.52" \
        "alembic==1.19.1" \
        "psycopg[binary]==3.3.4" \
        "pyodbc==5.3.0" \
        "httpx==0.28.1" \
        "itsdangerous==2.2.0" \
        "redis==8.1.0" \
        "PyJWT[crypto]==2.13.0" \
        "cryptography==50.0.1" \
        "numpy==2.4.6" \
        "pandas==3.0.5" \
        "yfinance==1.6.0"

COPY backend/. .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
