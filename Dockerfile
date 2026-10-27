FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 EPOCH_DB_PATH=/data/eviepoch-v1.sqlite3
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
COPY eviepoch ./eviepoch
COPY THIRD_PARTY_NOTICES.md ./THIRD_PARTY_NOTICES.md
COPY third_party ./third_party
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "eviepoch.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
