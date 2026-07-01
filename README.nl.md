# SMTP Relay voor Office 365

Een gecontaineriseerde Postfix SMTP relay die mail doorstuurt naar Office 365 via OAuth2 (client credentials flow). Inkomende verbindingen worden geaccepteerd op poort 25 en 587 met optionele TLS en SASL-authenticatie.

> Engelse documentatie: [README.md](README.md)

---

## Architectuur

```
Lokale applicaties
      │
      │  SMTP (poort 25 / 587)
      │  TLS: optioneel of verplicht (instelbaar)
      │  Auth: netwerktrust of SASL (sasldb2)
      ▼
┌─────────────────────┐
│  Postfix container  │
│                     │
│  graph-send.py      │──── token.json (elke 45 min ververst)
│  get-oauth2-token   │──── Entra ID (MSAL, client credentials)
└─────────────────────┘
      │
      │  HTTPS
      │  Auth: OAuth2 (Bearer token, client credentials)
      ▼
Microsoft Graph sendMail API
```

---

## Vereisten

- Docker en Docker Compose
- Een Office 365-tenant met Exchange Online
- Een Entra ID App Registration (zie hieronder)
- Een dedicated relay-mailbox (bijv. `relay@example.com`) met een geldig Exchange Online-licentie of gedeelde mailbox

---

## Entra ID configuratie

### 1. App Registration aanmaken

1. Ga naar **Entra ID** → **App registrations** → **New registration**
2. Naam: bijv. `SMTP Relay`
3. Supported account types: **Accounts in this organizational directory only**
4. Redirect URI: leeg laten
5. Klik op **Register**

Noteer de **Application (client) ID** en de **Directory (tenant) ID** — beide zijn nodig.

### 2. API-rechten toevoegen

De benodigde rechten zijn afhankelijk van de instelling `GRAPH_LARGE_ATTACHMENTS` (zie configuratieverwijzing):

**`GRAPH_LARGE_ATTACHMENTS=false` — eenvoudig pad, alleen Mail.Send:**

1. Ga naar **API permissions** → **Add a permission**
2. Kies **Microsoft Graph** → **Application permissions**
3. Voeg toe: `Mail.Send`
4. Klik op **Grant admin consent for [uw organisatie]** en bevestig

**`GRAPH_LARGE_ATTACHMENTS=true` (standaard) — grote-bijlage-ondersteuning, beide rechten vereist:**

1. Ga naar **API permissions** → **Add a permission**
2. Kies **Microsoft Graph** → **Application permissions**
3. Voeg beide toe:
   - `Mail.Send` — recht om mail te verzenden als een mailbox in de Application Access Policy-scopegroep
   - `Mail.ReadWrite` — nodig voor de concept + upload-sessie-flow bij bijlagen > 3 MB
4. Klik op **Add permissions**
5. Klik op **Grant admin consent for [uw organisatie]** en bevestig

> `Mail.ReadWrite` is nodig omdat grote bijlagen worden verzonden via een drietraps-flow (concept aanmaken → bijlage in chunks uploaden → concept verzenden), wat lees-/schrijftoegang tot de mailbox vereist. Beide rechten worden via een Application Access Policy beperkt tot specifieke mailboxen (stap 4) en gelden dus niet voor de hele tenant.

### 3a. Certificaatauthenticatie (aanbevolen)

Genereer een certificaat als u er nog geen heeft:

```bash
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/smtp.key \
    -out ./certs/smtp.crt \
    -days 3650 -nodes \
    -subj "/CN=smtp-relay"
```

Upload het publieke certificaat naar Entra ID:

1. Ga naar de App Registration → **Certificates & secrets** → **Certificates**
2. Klik op **Upload certificate** en selecteer `smtp.crt`
3. Noteer de getoonde thumbprint (ter referentie; MSAL leest deze automatisch)

Stel in `.env` in:
```env
OAUTH2_AUTH_TYPE=certificate
OAUTH2_CERT_PATH=/certs/smtp.crt
OAUTH2_KEY_PATH=/certs/smtp.key
```

### 3b. Client secret authenticatie

1. Ga naar de App Registration → **Certificates & secrets** → **Client secrets**
2. Klik op **New client secret**, stel een vervaldatum in, klik op **Add**
3. Kopieer de **Value** direct — deze wordt slechts eenmaal getoond

Stel in `.env` in:
```env
OAUTH2_AUTH_TYPE=secret
OAUTH2_CLIENT_SECRET=uw-secret-waarde-hier
```

### 4. Graph-rechten scopeten in Exchange Online

De Graph-rechten uit stap 2 gelden standaard voor de hele tenant. Beperk ze met een **Application Access Policy** tot uitsluitend de mailboxen in `RELAY_FROM_ADDRESSES`.

U heeft de **Application (client) ID** nodig van de overzichtspagina van de App Registration in Entra ID.

Voer het volgende uit in **Exchange Online PowerShell**:

```powershell
Connect-ExchangeOnline

$appId = "<application-client-id>"

# Maak een mail-enabled security group aan met precies de mailboxen uit RELAY_FROM_ADDRESSES
New-DistributionGroup -Name "SMTP Relay Mailboxen" -Type Security -Members relay@example.com, noreply@example.com

# Beperk de Graph-rechten van de app tot uitsluitend die groep
New-ApplicationAccessPolicy -AppId $appId -PolicyScopeGroupId "SMTP Relay Mailboxen" -AccessRight RestrictAccess -Description "SMTP relay - Graph Mail.Send/Mail.ReadWrite"

# Verifieer
Test-ApplicationAccessPolicy -AppId $appId -Identity relay@example.com    # moet Passed teruggeven
Test-ApplicationAccessPolicy -AppId $appId -Identity other@example.com    # moet Failed teruggeven
```

Een mailbox later toevoegen: `Add-DistributionGroupMember -Identity "SMTP Relay Mailboxen" -Member nieuwadres@example.com`

**Toegang intrekken:**

```powershell
# Zoek het policy-ID op
Get-ApplicationAccessPolicy | Where-Object { $_.AppId -eq $appId } | Format-List

# Verwijder de policy (hiermee vervalt de beperking — verwijder ook de Mail.Send/Mail.ReadWrite-rechten in Entra ID)
Remove-ApplicationAccessPolicy -Identity <policy-id>
```

---

## TLS-certificaat voor inkomende verbindingen

Voor TLS op inkomende verbindingen (poort 25/587) verwacht de container een certificaat op de paden ingesteld via `OAUTH2_CERT_PATH` en `OAUTH2_KEY_PATH` — of u kunt het Entra ID-certificaat hergebruiken.

Voor een zelfondertekend inbound certificaat:

```bash
mkdir -p ./certs
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/smtp.key \
    -out ./certs/smtp.crt \
    -days 3650 -nodes \
    -subj "/CN=mail.example.com"
```

---

## Uitrollen

**1. Kopieer en bewerk het omgevingsbestand:**

```bash
cp .env.example .env
```

Bewerk `.env` — stel minimaal in:

```env
RELAY_HOSTNAME=mail.example.com
RELAY_ALLOWED_NETWORKS=10.0.0.0/8
RELAY_FROM_ADDRESSES=relay@example.com
OAUTH2_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OAUTH2_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OAUTH2_AUTH_TYPE=certificate
```

**2. Bouwen en starten:**

```bash
docker compose up -d --build
```

**3. Verifiëren:**

```bash
# Bekijk container-logs
docker logs postfix-relay

# Test SMTP-verbinding
echo "Testmail" | mail -s "Test" -S smtp=smtp://localhost:25 ontvanger@example.com

# Bekijk de mailwachtrij
docker exec postfix-relay mailq
```

---

## Inbound SASL-gebruikersbeheer

De relay accepteert optioneel geauthenticeerde SMTP-verbindingen op poort 587 via een sasldb2-gebruikersdatabase. De database wordt buiten de container opgeslagen via de `SASLDB_DIR` bind mount.

```bash
# Gebruiker toevoegen of bijwerken (vraagt om wachtwoord)
docker exec -it postfix-relay manage-users.sh add jan example.com

# Alle gebruikers weergeven
docker exec -it postfix-relay manage-users.sh list

# Gebruiker verwijderen
docker exec -it postfix-relay manage-users.sh delete jan example.com
```

> SASL-authenticatie over plaintext is standaard geblokkeerd. Stel `SUBMISSION_TLS_LEVEL=encrypt` in in `.env` om TLS te verplichten vóór SASL op poort 587.

---

## Configuratieverwijzing

| Variabele | Verplicht | Standaard | Omschrijving |
|---|---|---|---|
| `RELAY_HOSTNAME` | ja | — | Hostnaam in SMTP EHLO/HELO |
| `RELAY_ALLOWED_NETWORKS` | ja | — | Netwerken die mogen relayeren zonder authenticatie (spatie-gescheiden CIDR) |
| `RELAY_FROM_ADDRESSES` | ja | — | Spatie-gescheiden lijst van specifieke mailboxadressen die mogen relayeren (geen domeinwildcards). Elk adres moet lid zijn van de Application Access Policy-scopegroep in Exchange Online. |
| `OAUTH2_TENANT_ID` | ja | — | Entra ID tenant ID |
| `OAUTH2_CLIENT_ID` | ja | — | Client ID van de app registration |
| `OAUTH2_AUTH_TYPE` | ja | — | `certificate` of `secret` |
| `OAUTH2_CERT_PATH` | bij certificate | `/certs/smtp.crt` | Pad naar certificaat in de container |
| `OAUTH2_KEY_PATH` | bij certificate | `/certs/smtp.key` | Pad naar privésleutel in de container |
| `OAUTH2_CLIENT_SECRET` | bij secret | — | Waarde van het client secret |
| `LISTEN_ADDRESS` | nee | `0.0.0.0` | Host-IP om op te binden |
| `SMTP_PORT` | nee | `25` | Host-poort gekoppeld aan containerpoort 25 |
| `SUBMISSION_PORT` | nee | `587` | Host-poort gekoppeld aan containerpoort 587 |
| `SMTP_TLS_LEVEL` | nee | `may` | Inbound TLS op poort 25: `none`, `may`, `encrypt` |
| `SUBMISSION_TLS_LEVEL` | nee | `may` | Inbound TLS op poort 587: `none`, `may`, `encrypt` |
| `CERTS_DIR` | nee | `./certs` | Hostmap met `smtp.crt` en `smtp.key` |
| `SASLDB_DIR` | nee | `./sasldb` | Hostmap voor de sasldb2-gebruikersdatabase |
| `LOG_LEVEL` | nee | `info` | Logverbositeit: `error` (alleen fouten), `info` (inbound TLS-samenvattingen + één regel per afgeleverd bericht), `debug` (volledige inbound TLS + Graph API-request/response-logging), `verbose` (als debug + access token gelogd bij elke tokenophaling — niet gebruiken in productie) |
| `GRAPH_SAVE_TO_SENT_ITEMS` | nee | `false` | Of relay-mail in de map Verzonden items verschijnt van de afzendermailbox in Office 365. Zet op `true` voor een audit-trail zichtbaar in Outlook/OWA. |
| `GRAPH_LARGE_ATTACHMENTS` | nee | `true` | Of bijlagen > 3 MB worden ondersteund via de Graph concept + upload-sessie-flow. `true` vereist zowel `Mail.Send` als `Mail.ReadWrite` in Entra ID. `false` gebruikt altijd het eenvoudige `sendMail`-pad (alleen `Mail.Send` nodig); bijlagen die de Graph-requestlimiet van ~4 MB overschrijden worden geweigerd. |

---

## Beveiligingspunten

- **SASL over plaintext is geblokkeerd** — `smtpd_sasl_security_options = noanonymous, noplaintext` voorkomt PLAIN/LOGIN zonder TLS. Gebruik `SUBMISSION_TLS_LEVEL=encrypt` op poort 587 om TLS end-to-end te verplichten.
- **Tokenbestandpermissies** — `token.json` wordt geschreven als `root:postfix 640`, leesbaar alleen door root en de groep `postfix`. Het bestand wordt opgeslagen in het `postfix-queue` volume. De access token wordt door `graph-send.py` nooit in logs geschreven; `get-oauth2-token.py` logt de token alleen bij `LOG_LEVEL=verbose`.
- **Netwerkbeperking** — poort 25 staat alleen `mynetworks` toe. Geauthenticeerde clients kunnen bovendien via poort 587 relayeren.
- **Beperk de Entra ID-app** — `Mail.Send` en `Mail.ReadWrite` worden via de Application Access Policy (stap 4) beperkt tot specifieke mailboxen. Voeg geen mailboxen toe aan de scopegroep die de relay niet nodig heeft. `Mail.ReadWrite` geeft leestoegang tot de mailinhoud van gescopete mailboxen (nodig voor de grote-bijlage-flow) — houd `RELAY_FROM_ADDRESSES` beperkt tot adressen die daadwerkelijk voor verzenden worden gebruikt.
- **Roteer certificaten en secrets** — stel een herinnering in vóór het verlopen van `OAUTH2_AUTH_TYPE=certificate`-certificaten (`-days 3650` = 10 jaar voor zelfondertekend). Client secrets verlopen op het schema ingesteld in Entra ID.

---

## Licentie

Dit project valt onder de MIT-licentie — zie [LICENSE](LICENSE) voor de volledige tekst.

Licenties van gebruikte derde componenten staan in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## Auteur

Gebouwd door **Mark Bartelen** bij [QC Tech](https://qctech.nl).
