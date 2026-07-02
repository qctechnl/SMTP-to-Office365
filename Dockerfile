FROM debian:12-slim

LABEL org.opencontainers.image.title="SMTP Relay for Office 365" \
      org.opencontainers.image.description="Postfix SMTP relay for Office 365 — delivers via Microsoft Graph API using Entra ID client credentials" \
      org.opencontainers.image.authors="Mark Bartelen <info@qctech.nl>" \
      org.opencontainers.image.vendor="QC Tech" \
      org.opencontainers.image.url="https://qctech.nl" \
      org.opencontainers.image.source="https://github.com/qctechnl/SMTP-to-Office365" \
      org.opencontainers.image.licenses="MIT"

RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    postfix \
    rsyslog \
    sasl2-bin \
    libsasl2-modules \
    ca-certificates \
    python3 \
    python3-pip \
    && pip3 install --no-cache-dir --break-system-packages \
        "msal>=1.29.0,<2.0.0" \
        "cryptography>=42.0.0,<45.0.0" \
        "requests>=2.31.0,<3.0.0" \
    && apt-get purge -y python3-pip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -s /sbin/nologin graph-send \
    && mkdir -p /etc/postfix/sasl \
               /etc/sasl2 \
               /var/lib/sasl2 \
               /certs

COPY postfix/main.cf                         /etc/postfix/main.cf
COPY postfix/master.cf                       /etc/postfix/master.cf
COPY --chmod=755 postfix/entrypoint.sh   /entrypoint.sh
COPY --chmod=755 postfix/manage-users.sh /usr/local/bin/manage-users.sh
COPY --chmod=644 postfix/graph-send.py   /usr/local/bin/graph-send.py

EXPOSE 25 587

ENTRYPOINT ["/entrypoint.sh"]
