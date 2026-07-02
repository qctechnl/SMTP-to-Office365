# SMTP Relay for Office 365

[![Docker Pulls](https://img.shields.io/docker/pulls/qctechnl/smtp-to-office365)](https://hub.docker.com/r/qctechnl/smtp-to-office365)
[![Docker Image Version](https://img.shields.io/docker/v/qctechnl/smtp-to-office365?sort=semver)](https://hub.docker.com/r/qctechnl/smtp-to-office365/tags)
[![Image Size](https://img.shields.io/docker/image-size/qctechnl/smtp-to-office365/latest)](https://hub.docker.com/r/qctechnl/smtp-to-office365/tags)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/qctechnl/SMTP-to-Office365/blob/main/LICENSE)

A containerised Postfix SMTP relay that lets legacy systems and appliances — anything that only speaks plain SMTP — send notifications and alerts through Microsoft 365 / Office 365.

Mail is delivered via the Microsoft Graph API (`sendMail`), authenticated with Entra ID using the OAuth2 client-credentials flow (client secret or certificate). There is no SMTP AUTH to Microsoft and no dependency on Basic Authentication.

Access is limited to an explicit list of sender mailboxes, enforced both at the SMTP layer and by RBAC for Applications in Exchange Online. It is not a tenant-wide open relay.

## Features

- Delivery through Microsoft Graph, so it keeps working after Basic Auth / SMTP AUTH is disabled in the tenant.
- Entra ID client-credentials authentication via secret or certificate. The access token is cached and never written to the logs.
- Permissions scoped to specific mailboxes via RBAC for Applications, rather than a tenant-wide consent.
- Attachments larger than ~3 MB are sent through the Graph upload-session flow.
- Plain SMTP on port 25 (from allowed networks) and authenticated submission on port 587 (SASL).
- Debian-slim based, built for `linux/amd64` and `linux/arm64`.

## Supported tags

- `latest` — most recent release
- `1`, `1.2`, `1.2.3` — semantic-version tags (major / minor / exact)

Architectures: `linux/amd64`, `linux/arm64`

## Quick start

Docker Compose:

```yaml
services:
  smtp-to-office365:
    image: qctechnl/smtp-to-office365:latest
    container_name: smtp-to-office365
    restart: unless-stopped
    ports:
      - "25:25"
      - "587:587"
    environment:
      RELAY_HOSTNAME:         mail.example.com
      RELAY_ALLOWED_NETWORKS: 10.0.0.0/8
      RELAY_FROM_ADDRESSES:   relay@example.com
      ENTRA_TENANT_ID:        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
      ENTRA_CLIENT_ID:        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
      ENTRA_AUTH_TYPE:        secret
      ENTRA_CLIENT_SECRET:    your-client-secret-here
    volumes:
      - ./certs:/certs:ro
      - ./sasldb:/var/lib/sasl2
      - postfix-queue:/var/spool/postfix

volumes:
  postfix-queue:
```

```bash
docker compose up -d
```

Plain `docker run`:

```bash
docker run -d --name smtp-to-office365 --restart unless-stopped \
  -p 25:25 -p 587:587 \
  -e RELAY_HOSTNAME=mail.example.com \
  -e RELAY_ALLOWED_NETWORKS=10.0.0.0/8 \
  -e RELAY_FROM_ADDRESSES=relay@example.com \
  -e ENTRA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  -e ENTRA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  -e ENTRA_AUTH_TYPE=secret \
  -e ENTRA_CLIENT_SECRET=your-client-secret-here \
  qctechnl/smtp-to-office365:latest
```

Before the first send you need an Entra ID app registration and a scoped Graph permission granted via RBAC for Applications in Exchange Online. The full setup guide is on GitHub.

## Common configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `RELAY_HOSTNAME` | yes | — | Hostname used in SMTP EHLO/HELO |
| `RELAY_ALLOWED_NETWORKS` | yes | — | Networks allowed to relay without auth (space-separated CIDR) |
| `RELAY_FROM_ADDRESSES` | yes | — | Space-separated mailbox addresses allowed to send |
| `ENTRA_TENANT_ID` | yes | — | Entra ID tenant ID |
| `ENTRA_CLIENT_ID` | yes | — | App registration client ID |
| `ENTRA_AUTH_TYPE` | yes | — | `secret` or `certificate` |
| `ENTRA_CLIENT_SECRET` | if secret | — | Client secret value |
| `SMTP_TLS_LEVEL` / `SUBMISSION_TLS_LEVEL` | no | `may` | Inbound TLS: `none`, `may`, `encrypt` |
| `GRAPH_LARGE_ATTACHMENTS` | no | `true` | Support attachments > 3 MB |
| `LOG_LEVEL` | no | `info` | `error`, `info`, `debug`, `verbose` |

The full variable reference, certificate authentication, TLS setup and RBAC configuration are documented on GitHub.

## Documentation and source

Full setup guide, Entra ID / RBAC configuration and troubleshooting:
[github.com/qctechnl/SMTP-to-Office365](https://github.com/qctechnl/SMTP-to-Office365)

Dutch documentation is available in the repository ([README.nl.md](https://github.com/qctechnl/SMTP-to-Office365/blob/main/README.nl.md)).

## License

MIT — QC Tech (https://qctech.nl), built by Mark Bartelen.
