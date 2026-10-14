FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py records.py patients.json ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
