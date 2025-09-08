FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt /tmp/requirements.txt
ARG TARGETPLATFORM
ENV DEBIAN_FRONTEND=noninteractive

# Configure piwheels for Raspberry Pi (arm/v7) to get prebuilt wheels
RUN if [ "$TARGETPLATFORM" = "linux/arm/v7" ] || [ "$(dpkg --print-architecture)" = "armhf" ]; then \
      printf "[global]\nindex-url = https://www.piwheels.org/simple\nextra-index-url = https://pypi.org/simple\n" > /etc/pip.conf; \
    fi

# On arm/v7 (armhf/armel), drop pyarrow from bulk install to avoid source build
RUN if [ "$TARGETPLATFORM" = "linux/arm/v7" ] || [ "$(dpkg --print-architecture)" = "armhf" ] || [ "$(dpkg --print-architecture)" = "armel" ]; then \
      grep -Ev '^(pyarrow)($|[=<>])' /tmp/requirements.txt > /tmp/requirements-no-arrow.txt && mv /tmp/requirements-no-arrow.txt /tmp/requirements.txt; \
    fi

# System libs needed by geopandas/shapely/fiona/pyproj (and friends)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gdal-bin libgdal-dev \
        libgeos-dev \
        proj-bin libproj-dev \
        libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

# Prefer wheels to avoid source builds on non-amd64
ENV PIP_NO_CACHE_DIR=1
RUN python -m pip install --upgrade pip setuptools wheel
RUN pip install --prefer-binary -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Add a Parquet engine on arm/v7: prefer fastparquet (pyarrow lacks wheels there)
RUN if [ "$TARGETPLATFORM" = "linux/arm/v7" ] || [ "$(dpkg --print-architecture)" = "armhf" ] || [ "$(dpkg --print-architecture)" = "armel" ]; then \
      pip install --prefer-binary fastparquet || pip install --prefer-binary pyarrow; \
    fi

COPY app.py /app/app.py

EXPOSE 8501

ENV STAC_PATH=/data

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
