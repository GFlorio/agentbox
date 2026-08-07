FROM docker.io/library/ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG OPENCODE_VERSION=latest

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        fd-find \
        git \
        jq \
        less \
        nodejs \
        npm \
        procps \
        python3 \
        python3-pil \
        python3-numpy \
        ripgrep \
        shellcheck \
        software-properties-common \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /home/agent \
    && chown "${USER_ID}" /home/agent

RUN npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force

RUN mkdir -p /tmp/opencode \
    && chown "${USER_ID}" /tmp/opencode

COPY opencode.json /etc/opencode/opencode.json

ENV HOME=/home/agent
ENV XDG_CACHE_HOME=/home/agent/.cache
ENV XDG_CONFIG_HOME=/home/agent/.config
ENV XDG_DATA_HOME=/home/agent/.local/share
ENV XDG_STATE_HOME=/home/agent/.local/state

WORKDIR /workspace
USER ${USER_ID}:${GROUP_ID}

CMD ["opencode", "/workspace"]
