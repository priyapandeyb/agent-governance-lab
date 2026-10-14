FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py a2a_main.py __init__.py ./
ENV PYTHONUNBUFFERED=1
ENV ADK_SUPPRESS_A2A_EXPERIMENTAL_FEATURE_WARNINGS=true
CMD ["sh", "-c", "uvicorn a2a_main:a2a_app --host 0.0.0.0 --port ${PORT:-8080}"]
