# SMTP Relay for Office 365

[![Docker Pulls](https://img.shields.io/docker/pulls/qctechnl/smtp-to-office365)](https://hub.docker.com/r/qctechnl/smtp-to-office365)
[![Docker Image Version](https://img.shields.io/docker/v/qctechnl/smtp-to-office365?sort=semver)](https://hub.docker.com/r/qctechnl/smtp-to-office365/tags)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A containerised Postfix SMTP relay that forwards mail to Office 365 via OAuth2 (client credentials flow). Inbound connections are accepted on port 25 and 587 with optional TLS and SASL authentication.

> Dutch documentation: [README.nl.md](README.nl.md)

---

## Architecture

```
Local applications
      │
      │  SMTP (port 25 / 587)
      │  TLS: optional or mandatory (configurable)
      │  Auth: network trust or SASL (sasldb2)
      ▼
┌─────────────────────┐
│   Postfix container │
│                     │
│  graph-send.py      │──── Entra ID (MSAL, client credentials flow)
│                     │──── token.json (cached, refreshed on expiry)
└─────────────────────┘
      │
      │  HTTPS
      │  Auth: OAuth2 (Bearer token, client credentials)
      ▼
Microsoft Graph sendMail API
```

---

## Prerequisites

- Docker and Docker Compose
- An Office 365 tenant with Exchange Online
- An Entra ID App Registration (see below)
- A dedicated relay mailbox (e.g. `relay@example.com`) with a valid Exchange Online licence or shared mailbox

---

## Entra ID configuration

### 1. Create an App Registration

1. Go to **Entra ID** → **App registrations** → **New registration**
2. Name: e.g. `SMTP Relay`
3. Supported account types: **Accounts in this organizational directory only**
4. Redirect URI: leave empty
5. Click **Register**

Note the **Application (client) ID** and **Directory (tenant) ID** — you will need both.

### 2. Add API permissions

The required permissions depend on the `GRAPH_LARGE_ATTACHMENTS` setting (see configuration reference):

**`GRAPH_LARGE_ATTACHMENTS=false` — simple path, Mail.Send only:**

1. Go to **API permissions** → **Add a permission**
2. Choose **Microsoft Graph** → **Application permissions**
3. Add: `Mail.Send`
4. Click **Grant admin consent for [your organisation]** and confirm

**`GRAPH_LARGE_ATTACHMENTS=true` (default) — large attachment support, both permissions required:**

1. Go to **API permissions** → **Add a permission**
2. Choose **Microsoft Graph** → **Application permissions**
3. Add both:
   - `Mail.Send` — permission to send mail as any mailbox in the Application Access Policy scope group
   - `Mail.ReadWrite` — required for the draft + chunked upload flow used for attachments > 3 MB
4. Click **Add permissions**
5. Click **Grant admin consent for [your organisation]** and confirm

> `Mail.ReadWrite` is needed because large attachments are sent by first creating a draft message, uploading the attachment in chunks via an upload session, and then sending the draft — operations that require read/write access to the mailbox. Both permissions are scoped to specific mailboxes via an Application Access Policy (step 4), so they do not grant access to the entire tenant.

### 3a. Certificate authentication (recommended)

Generate a certificate if you do not already have one:

```bash
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/entra.key \
    -out ./certs/entra.crt \
    -days 3650 -nodes \
    -subj "/CN=smtp-relay"
```

Upload the public certificate to Entra ID:

1. Go to the App Registration → **Certificates & secrets** → **Certificates**
2. Click **Upload certificate** and select `entra.crt`
3. Copy the displayed thumbprint (for reference; MSAL reads it automatically)

Set in `.env`:
```env
ENTRA_AUTH_TYPE=certificate
ENTRA_CERT_PATH=/certs/entra.crt
ENTRA_KEY_PATH=/certs/entra.key
```

### 3b. Client secret authentication

1. Go to the App Registration → **Certificates & secrets** → **Client secrets**
2. Click **New client secret**, set an expiry, click **Add**
3. Copy the **Value** immediately — it is only shown once

Set in `.env`:
```env
ENTRA_AUTH_TYPE=secret
ENTRA_CLIENT_SECRET=your-secret-value-here
```

### 4. Scope the app's Graph permissions in Exchange Online

The Graph permissions granted in step 2 are tenant-wide by default. Use an **Application Access Policy** in Exchange Online to restrict them to only the mailboxes in `RELAY_FROM_ADDRESSES`.

You need the **Application (client) ID** from the App Registration overview in Entra ID.

Run the following in **Exchange Online PowerShell**:

```powershell
Connect-ExchangeOnline

$appId = "<application-client-id>"

# Create a mail-enabled security group containing exactly the mailboxes in RELAY_FROM_ADDRESSES
New-DistributionGroup -Name "SMTP Relay Mailboxes" -Type Security -Members relay@example.com, noreply@example.com

# Restrict the app's Graph permissions to that group only
New-ApplicationAccessPolicy -AppId $appId -PolicyScopeGroupId "SMTP Relay Mailboxes" -AccessRight RestrictAccess -Description "SMTP relay - Graph Mail.Send/Mail.ReadWrite"

# Verify
Test-ApplicationAccessPolicy -AppId $appId -Identity relay@example.com    # should return Passed
Test-ApplicationAccessPolicy -AppId $appId -Identity other@example.com    # should return Failed
```

To add a mailbox later: `Add-DistributionGroupMember -Identity "SMTP Relay Mailboxes" -Member newaddress@example.com`

**To revoke all access:**

```powershell
# Find the policy ID
Get-ApplicationAccessPolicy | Where-Object { $_.AppId -eq $appId } | Format-List

# Remove the policy (restores tenant-wide access — also remove the Mail.Send/Mail.ReadWrite app permissions in Entra ID)
Remove-ApplicationAccessPolicy -Identity <policy-id>
```

---

## TLS certificate for inbound connections

This relay uses two separate certificates that serve completely different purposes:

| Variable | Purpose |
|---|---|
| `SMTP_TLS_CERT_PATH` / `SMTP_TLS_KEY_PATH` | Encrypts **inbound** SMTP connections from local applications to this relay |
| `ENTRA_CERT_PATH` / `ENTRA_KEY_PATH` | Authenticates this relay **outbound** to Microsoft Entra ID (only when `ENTRA_AUTH_TYPE=certificate`) |

Generate a self-signed certificate for inbound TLS:

```bash
mkdir -p ./certs
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/smtp.key \
    -out ./certs/smtp.crt \
    -days 3650 -nodes \
    -subj "/CN=mail.example.com"
```

Both certificates are placed in `CERTS_DIR` (mounted as `/certs` in the container). They can point to the same file if desired, but by default use separate paths (`smtp.crt`/`smtp.key` for inbound TLS, `entra.crt`/`entra.key` for Entra ID auth).

---

## Deployment

**1. Copy and edit the environment file:**

```bash
cp .env.example .env
```

> The image is available on Docker Hub — `docker compose up -d` pulls it automatically. To build locally instead, run `docker compose up -d --build`.

Edit `.env` — at minimum set:

```env
RELAY_HOSTNAME=mail.example.com
RELAY_ALLOWED_NETWORKS=10.0.0.0/8
RELAY_FROM_ADDRESSES=relay@example.com
ENTRA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_AUTH_TYPE=certificate
```

**2. Build and start:**

```bash
docker compose up -d --build
```

**3. Verify:**

```bash
# Check container logs
docker logs postfix-relay

# Test SMTP connectivity
echo "Test mail" | mail -s "Test" -S smtp=smtp://localhost:25 recipient@example.com

# Check mail queue
docker exec postfix-relay mailq
```

---

## Inbound SASL user management

The relay optionally accepts authenticated SMTP connections on port 587 using a sasldb2 user database. The database is stored outside the container via the `SASLDB_DIR` bind mount.

```bash
# Add or update a user (prompts for password)
docker exec -it postfix-relay manage-users.sh add john example.com

# List all users
docker exec -it postfix-relay manage-users.sh list

# Delete a user
docker exec -it postfix-relay manage-users.sh delete john example.com
```

> SASL authentication over plaintext is blocked by default. Set `SUBMISSION_TLS_LEVEL=encrypt` in `.env` to enforce TLS before allowing SASL on port 587.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `RELAY_HOSTNAME` | yes | — | Hostname used in SMTP EHLO/HELO |
| `RELAY_ALLOWED_NETWORKS` | yes | — | Networks allowed to relay without auth (space-separated CIDR) |
| `RELAY_FROM_ADDRESSES` | yes | — | Space-separated list of specific mailbox addresses allowed to relay (no domain wildcards). Each must be a member of the Application Access Policy scope group in Exchange Online. |
| `ENTRA_TENANT_ID` | yes | — | Entra ID tenant ID |
| `ENTRA_CLIENT_ID` | yes | — | App registration client ID |
| `ENTRA_AUTH_TYPE` | yes | — | `certificate` or `secret` |
| `ENTRA_CERT_PATH` | if certificate | — | Path inside container to the Entra ID client certificate (not the inbound TLS cert) |
| `ENTRA_KEY_PATH` | if certificate | — | Path inside container to the Entra ID private key |
| `ENTRA_CLIENT_SECRET` | if secret | — | Client secret value |
| `LISTEN_ADDRESS` | no | `0.0.0.0` | Host IP to bind on |
| `SMTP_PORT` | no | `25` | Host port mapped to container port 25 |
| `SUBMISSION_PORT` | no | `587` | Host port mapped to container port 587 |
| `SMTP_TLS_LEVEL` | no | `may` | Inbound TLS on port 25: `none`, `may`, `encrypt` |
| `SUBMISSION_TLS_LEVEL` | no | `may` | Inbound TLS on port 587: `none`, `may`, `encrypt` |
| `SMTP_TLS_CERT_PATH` | no | `/certs/smtp.crt` | Path inside container to the inbound SMTP TLS certificate |
| `SMTP_TLS_KEY_PATH` | no | `/certs/smtp.key` | Path inside container to the inbound SMTP TLS private key |
| `CERTS_DIR` | no | `./certs` | Host directory mounted as `/certs`; place both the inbound TLS cert and (when applicable) the Entra ID cert here |
| `SASLDB_DIR` | no | `./sasldb` | Host directory for the sasldb2 user database |
| `LOG_LEVEL` | no | `info` | Log verbosity: `error` (errors only), `info` (inbound TLS summaries + one line per delivered message), `debug` (full inbound TLS + Graph API request/response logging), `verbose` (same as debug + access token logged on every fetch — do not use in production) |
| `GRAPH_SAVE_TO_SENT_ITEMS` | no | `false` | Whether relay-sent mail appears in the sender's Sent Items folder in Office 365. Set to `true` for an audit trail visible in Outlook/OWA. |
| `GRAPH_LARGE_ATTACHMENTS` | no | `true` | Whether to support attachments > 3 MB via the Graph draft + chunked upload flow. `true` requires both `Mail.Send` and `Mail.ReadWrite` in Entra ID. `false` uses the simple `sendMail` path only (`Mail.Send` sufficient); attachments exceeding Graph's ~4 MB request limit will bounce. |

---

## Security notes

- **SASL over plaintext is blocked** — `smtpd_sasl_security_options = noanonymous, noplaintext` prevents PLAIN/LOGIN without TLS. Use `SUBMISSION_TLS_LEVEL=encrypt` on port 587 to enforce TLS end-to-end.
- **Token file permissions** — `token.json` is written with mode `600`, owned by `graph-send:graph-send`. It is stored inside the `postfix-queue` volume and readable only by the pipe transport user. The access token is never written to logs; `graph-send.py` logs it only at `LOG_LEVEL=verbose` with an explicit warning.
- **Network restriction** — port 25 only permits `mynetworks`. Authenticated clients can additionally relay via port 587.
- **Restrict the Entra ID app** — `Mail.Send` and `Mail.ReadWrite` are scoped to specific mailboxes via the Application Access Policy (step 4). Do not add mailboxes to the scope group that the relay does not need. Note that `Mail.ReadWrite` allows reading mail content of scoped mailboxes (needed for the large-attachment draft flow) — keep `RELAY_FROM_ADDRESSES` limited to addresses actually used for sending.
- **Rotate certificates and secrets** — set a calendar reminder before `ENTRA_AUTH_TYPE=certificate` certificates expire (`-days 3650` = 10 years for self-signed). Client secrets expire on the schedule set in Entra ID.

---

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

Third-party component licenses are listed in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## Author

Built by **Mark Bartelen** at [QC Tech](https://qctech.nl).
