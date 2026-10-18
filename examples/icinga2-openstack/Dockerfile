# check=skip=InvalidDefaultArgInFrom

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG OPENSTACK_RELEASE=2025.1

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && python3 -m venv /opt/icinga-venv \
    && /opt/icinga-venv/bin/pip install --no-cache-dir \
       -c "https://releases.openstack.org/constraints/upper/${OPENSTACK_RELEASE}" \
       python-openstackclient python-masakariclient python-octaviaclient \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER icinga
