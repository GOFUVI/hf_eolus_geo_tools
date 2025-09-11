FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt /tmp/requirements.txt
ENV DEBIAN_FRONTEND=noninteractive

# System libs needed by geopandas/shapely/fiona/pyproj (and friends)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gdal-bin libgdal-dev \
        libgeos-dev \
        proj-bin libproj-dev \
        libspatialindex-dev \
        python3-numpy python3-pandas \
    && rm -rf /var/lib/apt/lists/*

# Prefer wheels to avoid source builds on non-amd64
ENV PIP_NO_CACHE_DIR=1
RUN python -m pip install --upgrade pip setuptools wheel
ENV PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH
RUN pip install --prefer-binary --upgrade-strategy only-if-needed -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Mitigate potential OpenBLAS CPU detection issues on some ARM SoCs (e.g., RPi)
# This forces a safe generic ARMv8 kernel to avoid SIGILL on import of NumPy/Pandas
ENV OPENBLAS_CORETYPE=ARMV8

COPY app.py /app/app.py

EXPOSE 8501

ENV STAC_PATH=/data

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
