FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV OUTPUT_DIR=/data/output DB_URL=sqlite+aiosqlite:////data/engine.db U2NET_HOME=/data/models
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
