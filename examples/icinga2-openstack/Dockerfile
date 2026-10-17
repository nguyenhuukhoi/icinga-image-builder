# check=skip=InvalidDefaultArgInFrom

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG OPENSTACKSDK_VERSION=4.21.0
ARG PYYAML_VERSION=6.0.3

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && python3 -m venv /opt/icinga-venv \
    && /opt/icinga-venv/bin/pip install --no-cache-dir \
       "openstacksdk==${OPENSTACKSDK_VERSION}" "PyYAML==${PYYAML_VERSION}" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER icinga
