FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.txt

COPY app.py /app/app.py

EXPOSE 8501

ENV STAC_PATH=/data

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
