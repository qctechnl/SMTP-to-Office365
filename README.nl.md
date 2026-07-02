# SMTP Relay voor Office 365

[![Docker Pulls](https://img.shields.io/docker/pulls/qctechnl/smtp-to-office365)](https://hub.docker.com/r/qctechnl/smtp-to-office365)
[![Docker Image Version](https://img.shields.io/docker/v/qctechnl/smtp-to-office365?sort=semver)](https://hub.docker.com/r/qctechnl/smtp-to-office365/tags)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Een gecontaineriseerde Postfix SMTP relay die mail aflevert bij Office 365 via de Microsoft Graph API (`sendMail`), geauthenticeerd met Entra ID (OAuth2 client credentials — secret of certificaat). Inkomende verbindingen worden geaccepteerd op poort 25 en 587 met optionele TLS en SASL-authenticatie.

**Bedoeld gebruik:** legacy systemen en apparaten (die alleen platte SMTP spreken) op een veilige manier notificaties en meldingen laten versturen via Entra ID, namens een klein, expliciet opgesomd aantal afzendermailboxen. Het is bewust **geen** algemene relay voor de hele organisatie — toegang is beperkt tot de mailboxen in `RELAY_FROM_ADDRESSES`, afgedwongen zowel op SMTP-niveau als door RBAC for Applications in Exchange Online (zie stap 4).

> Engelse documentatie: [README.md](README.md)

---

## Architectuur

```
Lokale applicaties
      │
      │  SMTP (poort 25 / 587)
      │  TLS: optioneel of verplicht (instelbaar)
      │  Auth: Geauthenticeerd of afkomstig van toegestaan netwerk (ongeauthenticeerd)
      ▼
┌─────────────────────┐
│  Postfix container  │
│                     │
│  graph-send.py      │──── Entra ID (MSAL, client credentials flow)
│                     │──── token.json (gecached, ververst bij verlopen)
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
2. Naam: bijv. `SMTP-to-Office365 Relay`
3. Supported account types: **Accounts in this organizational directory only**
4. Redirect URI: leeg laten
5. Klik op **Register**

Noteer de **Application (client) ID** en de **Directory (tenant) ID** — beide zijn nodig.

### 2. API-rechten — verleen **geen** Graph-rechten in Entra ID

Deze relay verleent zijn Graph-rechten via **RBAC for Applications** in Exchange Online (stap 4), gescopet tot specifieke mailboxen. Dat mechanisme verleent het recht zelf — u hoeft dus **geen** Microsoft Graph application permission toe te voegen of te consenten op de App Registration.

> Verleen **geen** tenant-brede admin consent voor `Mail.Send` of `Mail.ReadWrite` in Entra ID. Entra ID-consent en Exchange RBAC zijn **additief** (een union), dus een tenant-brede consent omzeilt de RBAC-scoping en geeft de app toegang tot élke mailbox. Laat de **API permissions** van de App Registration leeg en vertrouw volledig op de gescopete RBAC-toewijzing in stap 4.

Welke Graph-rechten de RBAC-rol moet dekken hangt af van de instelling `GRAPH_LARGE_ATTACHMENTS` (zie configuratieverwijzing), wat overeenkomt met de rol die u in stap 4 toewijst:

- **`GRAPH_LARGE_ATTACHMENTS=false`** — eenvoudig `sendMail`-pad, alleen `Mail.Send` nodig → rol `Application Mail.Send`
- **`GRAPH_LARGE_ATTACHMENTS=true`** (standaard) — grote-bijlage-ondersteuning, `Mail.Send` **én** `Mail.ReadWrite` nodig → rol `Application Mail Full Access`

> `Mail.ReadWrite` is nodig voor grote bijlagen omdat die worden verzonden via een drietraps-flow (concept aanmaken → bijlage in chunks uploaden → concept verzenden), wat lees-/schrijftoegang tot de mailbox vereist.

### 3a. Certificaatauthenticatie (aanbevolen)

Genereer een certificaat als u er nog geen heeft:

```bash
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/entra.key \
    -out ./certs/entra.crt \
    -days 3650 -nodes \
    -subj "/CN=smtp-to-office365"
```

Upload het publieke certificaat naar Entra ID:

1. Ga naar de App Registration → **Certificates & secrets** → **Certificates**
2. Klik op **Upload certificate** en selecteer `entra.crt`
3. Noteer de getoonde thumbprint (ter referentie; MSAL leest deze automatisch)

Stel in `.env` in:
```env
ENTRA_AUTH_TYPE=certificate
ENTRA_CERT_PATH=/certs/entra.crt
ENTRA_KEY_PATH=/certs/entra.key
```

### 3b. Client secret authenticatie

1. Ga naar de App Registration → **Certificates & secrets** → **Client secrets**
2. Klik op **New client secret**, stel een vervaldatum in, klik op **Add**
3. Kopieer de **Value** direct — deze wordt slechts eenmaal getoond

Stel in `.env` in:
```env
ENTRA_AUTH_TYPE=secret
ENTRA_CLIENT_SECRET=uw-secret-waarde-hier
```

### 4. Graph-rechten gescopet verlenen met RBAC for Applications

Gebruik **RBAC for Applications** in Exchange Online om de app een Graph-recht te verlenen dat gescopet is tot uitsluitend de mailboxen in `RELAY_FROM_ADDRESSES`. Dit vervangt het deprecated `New-ApplicationAccessPolicy`-mechanisme — de RBAC-rol verleent het recht zelf, dus er wordt niets tenant-breed geconsent in Entra ID.

U heeft twee ID's nodig uit **Entra ID → Enterprise applications** (open uw app dáár, niet op de App registrations-pagina — die toont andere waarden):

- **Application ID** — de client ID
- **Object ID** — het object-ID van de enterprise application (service principal)

Voer het volgende uit in **Exchange Online PowerShell**:

```powershell
Connect-ExchangeOnline

$appId = "<application-client-id>"

# 1. Maak een Exchange service principal die naar de enterprise application van de app wijst.
#    Object ID komt van Entra ID > Enterprise applications (NIET de App registrations-pagina).
New-ServicePrincipal -AppId $appId -ObjectId "<enterprise-app-object-id>" -DisplayName "SMTP-to-Office365 Relay"

# 2. Maak een mail-enabled security group met precies de mailboxen uit RELAY_FROM_ADDRESSES.
$group = New-DistributionGroup -Name "SMTP-to-Office365 Relay Mailboxen" -Type Security -Members relay@example.com, noreply@example.com

# 3. Maak een management scope die beperkt is tot DIRECTE leden van die groep.
#    MemberOfGroup vereist de distinguished name van de groep (geneste groepen vallen buiten scope).
New-ManagementScope -Name "SMTP-to-Office365 Relay Scope" -RecipientRestrictionFilter "MemberOfGroup -eq '$($group.DistinguishedName)'"

# 4. Wijs de gescopete application-rol toe.
#    GRAPH_LARGE_ATTACHMENTS=true (standaard) -> "Application Mail Full Access" (Mail.Send + Mail.ReadWrite)
#    GRAPH_LARGE_ATTACHMENTS=false             -> "Application Mail.Send"
New-ManagementRoleAssignment -App $appId -Role "Application Mail Full Access" -CustomResourceScope "SMTP-to-Office365 Relay Scope"

# 5. Verifieer (deze test-cmdlet omzeilt de permissie-cache).
Test-ServicePrincipalAuthorization -Identity $appId -Resource relay@example.com | Format-Table   # InScope moet True zijn
Test-ServicePrincipalAuthorization -Identity $appId -Resource other@example.com  | Format-Table   # InScope moet False zijn
```

Een mailbox later toevoegen: `Add-DistributionGroupMember -Identity "SMTP-to-Office365 Relay Mailboxen" -Member nieuwadres@example.com`

> **Propagatie:** RBAC-wijzigingen zijn actief na een cache-verversing van 30 minuten tot 2 uur. `Test-ServicePrincipalAuthorization` omzeilt die cache, dus gebruik die om de configuratie direct te verifiëren in plaats van te wachten tot een live verzending slaagt.

**Migreren vanaf een oude Application Access Policy?**

Als deze app eerder `New-ApplicationAccessPolicy` gebruikte, voltooi dan de RBAC-stappen hierboven en ruim daarna de oude configuratie op — dit lost tevens de fout `[RAOP] Blocked by tenant configured AppOnly AccessPolicy` (HTTP 403) op:

```powershell
# Verwijder eerst een eventuele tenant-brede Graph-consent in Entra ID
# (App Registration > API permissions), verwijder daarna de oude policy:
Get-ApplicationAccessPolicy | Where-Object { $_.AppId -eq $appId } | Format-List
Remove-ApplicationAccessPolicy -Identity <policy-id>
```

**Toegang volledig intrekken:**

Omdat er geen tenant-breed Graph-recht in Entra ID is geconsent, trekt het verwijderen van de RBAC-rol-toewijzing de toegang van de app volledig in:

```powershell
# Zoek de naam van de toewijzing op en verwijder die
Get-ManagementRoleAssignment -RoleAssignee $appId
Remove-ManagementRoleAssignment -Identity "<assignment-name>"

# Verwijder eventueel ook de service principal-pointer en de scope
Remove-ServicePrincipal -Identity $appId
Remove-ManagementScope -Identity "SMTP-to-Office365 Relay Scope"
```

---

## TLS-certificaat voor inkomende verbindingen

Deze relay gebruikt twee afzonderlijke certificaten met totaal verschillende doelen:

| Variabele | Doel |
|---|---|
| `SMTP_TLS_CERT_PATH` / `SMTP_TLS_KEY_PATH` | Versleutelt **inkomende** SMTP-verbindingen van lokale applicaties naar deze relay |
| `ENTRA_CERT_PATH` / `ENTRA_KEY_PATH` | Authenticeert deze relay **uitgaand** bij Microsoft Entra ID (alleen bij `ENTRA_AUTH_TYPE=certificate`) |

Genereer een zelfondertekend certificaat voor inkomende TLS:

```bash
mkdir -p ./certs
openssl req -x509 -newkey rsa:4096 \
    -keyout ./certs/smtp.key \
    -out ./certs/smtp.crt \
    -days 3650 -nodes \
    -subj "/CN=mail.example.com"
```

Beide certificaten worden in `CERTS_DIR` geplaatst (gemount als `/certs` in de container). Ze kunnen naar hetzelfde bestand wijzen, maar gebruiken standaard aparte paden (`smtp.crt`/`smtp.key` voor inkomende TLS, `entra.crt`/`entra.key` voor Entra ID-authenticatie).

---

## Uitrollen

**1. Kopieer en bewerk het omgevingsbestand:**

```bash
cp .env.example .env
```

> Het image staat op Docker Hub — `docker compose up -d` downloadt het automatisch. Lokaal bouwen kan met `docker compose up -d --build`.

Bewerk `.env` — stel minimaal in:

```env
RELAY_HOSTNAME=mail.example.com
RELAY_ALLOWED_NETWORKS=10.0.0.0/8
RELAY_FROM_ADDRESSES=relay@example.com
ENTRA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_AUTH_TYPE=secret
ENTRA_CLIENT_SECRET=your-secret-value-here
```

**2. Bouwen en starten:**

```bash
docker compose up -d --build
```

**3. Verifiëren:**

```bash
# Bekijk container-logs
docker logs smtp-to-office365

# Test SMTP-verbinding
echo "Testmail" | mail -s "Test" -S smtp=smtp://localhost:25 ontvanger@example.com

# Bekijk de mailwachtrij
docker exec smtp-to-office365 mailq
```

---

## Inbound SASL-gebruikersbeheer

De relay accepteert optioneel geauthenticeerde SMTP-verbindingen op poort 587 via een sasldb2-gebruikersdatabase. De database wordt buiten de container opgeslagen via de `SASLDB_DIR` bind mount.

```bash
# Gebruiker toevoegen of bijwerken (vraagt om wachtwoord)
docker exec -it smtp-to-office365 manage-users.sh add jan example.com

# Alle gebruikers weergeven
docker exec -it smtp-to-office365 manage-users.sh list

# Gebruiker verwijderen
docker exec -it smtp-to-office365 manage-users.sh delete jan example.com
```

> SASL-authenticatie over plaintext is standaard geblokkeerd. Stel `SUBMISSION_TLS_LEVEL=encrypt` in in `.env` om TLS te verplichten vóór SASL op poort 587.

---

## Configuratieverwijzing

| Variabele | Verplicht | Standaard | Omschrijving |
|---|---|---|---|
| `RELAY_HOSTNAME` | ja | — | Hostnaam in SMTP EHLO/HELO |
| `RELAY_ALLOWED_NETWORKS` | ja | — | Netwerken die mogen relayeren zonder authenticatie (spatie-gescheiden CIDR) |
| `RELAY_FROM_ADDRESSES` | ja | — | Spatie-gescheiden lijst van specifieke mailboxadressen die mogen relayeren (geen domeinwildcards). Elk adres moet lid zijn van de RBAC management scope-groep in Exchange Online (stap 4). |
| `ENTRA_TENANT_ID` | ja | — | Entra ID tenant ID |
| `ENTRA_CLIENT_ID` | ja | — | Client ID van de app registration |
| `ENTRA_AUTH_TYPE` | ja | — | `certificate` of `secret` |
| `ENTRA_CERT_PATH` | bij certificate | — | Pad in de container naar het Entra ID-clientcertificaat (niet het inbound TLS-certificaat) |
| `ENTRA_KEY_PATH` | bij certificate | — | Pad in de container naar de Entra ID-privésleutel |
| `ENTRA_CLIENT_SECRET` | bij secret | — | Waarde van het client secret |
| `LISTEN_ADDRESS` | nee | `0.0.0.0` | Host-IP om op te binden |
| `SMTP_PORT` | nee | `25` | Host-poort gekoppeld aan containerpoort 25 |
| `SUBMISSION_PORT` | nee | `587` | Host-poort gekoppeld aan containerpoort 587 |
| `SMTP_TLS_LEVEL` | nee | `may` | Inbound TLS op poort 25: `none`, `may`, `encrypt` |
| `SUBMISSION_TLS_LEVEL` | nee | `may` | Inbound TLS op poort 587: `none`, `may`, `encrypt` |
| `SMTP_TLS_CERT_PATH` | nee | `/certs/smtp.crt` | Pad in de container naar het inbound SMTP TLS-certificaat |
| `SMTP_TLS_KEY_PATH` | nee | `/certs/smtp.key` | Pad in de container naar de inbound SMTP TLS-privésleutel |
| `CERTS_DIR` | nee | `./certs` | Hostmap gemount als `/certs`; plaats hier zowel het inbound TLS-certificaat als (indien van toepassing) het Entra ID-certificaat |
| `SASLDB_DIR` | nee | `./sasldb` | Hostmap voor de sasldb2-gebruikersdatabase |
| `LOG_LEVEL` | nee | `info` | Logverbositeit: `error` (alleen fouten), `info` (inbound TLS-samenvattingen + één regel per afgeleverd bericht), `debug` (volledige inbound TLS + Graph API-request/response-logging), `verbose` (als debug + access token gelogd bij elke tokenophaling — niet gebruiken in productie) |
| `GRAPH_SAVE_TO_SENT_ITEMS` | nee | `false` | Of relay-mail in de map Verzonden items verschijnt van de afzendermailbox in Office 365. Zet op `true` voor een audit-trail zichtbaar in Outlook/OWA. |
| `GRAPH_LARGE_ATTACHMENTS` | nee | `true` | Of bijlagen > 3 MB worden ondersteund via de Graph concept + upload-sessie-flow. `true` vereist zowel `Mail.Send` als `Mail.ReadWrite` in Entra ID. `false` gebruikt altijd het eenvoudige `sendMail`-pad (alleen `Mail.Send` nodig); bijlagen die de Graph-requestlimiet van ~4 MB overschrijden worden geweigerd. |

---

## Beveiligingspunten

- **SASL over plaintext is geblokkeerd** — `smtpd_sasl_security_options = noanonymous, noplaintext` voorkomt PLAIN/LOGIN zonder TLS. Gebruik `SUBMISSION_TLS_LEVEL=encrypt` op poort 587 om TLS end-to-end te verplichten.
- **Tokenbestandpermissies** — `token.json` wordt geschreven met mode `600`, eigendom van `graph-send:graph-send`. Het bestand wordt opgeslagen in het `postfix-queue` volume en is uitsluitend leesbaar door de pipe transport-gebruiker. De access token wordt nooit in logs geschreven; `graph-send.py` logt de token uitsluitend bij `LOG_LEVEL=verbose` met een expliciete waarschuwing.
- **Netwerkbeperking** — poort 25 staat alleen `mynetworks` toe. Geauthenticeerde clients kunnen bovendien via poort 587 relayeren.
- **Beperk de Entra ID-app** — de Graph-rechten van de app worden gescopet tot specifieke mailboxen verleend via RBAC for Applications (`Application Mail Full Access` / `Application Mail.Send`, stap 4), niet via een tenant-brede Entra ID-consent. Voeg geen mailboxen toe aan de scopegroep die de relay niet nodig heeft. `Mail.ReadWrite` (onderdeel van `Application Mail Full Access`) geeft leestoegang tot de mailinhoud van gescopete mailboxen (nodig voor de grote-bijlage-flow) — houd `RELAY_FROM_ADDRESSES` beperkt tot adressen die daadwerkelijk voor verzenden worden gebruikt, of gebruik `GRAPH_LARGE_ATTACHMENTS=false` met de verzend-only rol `Application Mail.Send`.
- **Roteer certificaten en secrets** — stel een herinnering in vóór het verlopen van `ENTRA_AUTH_TYPE=certificate`-certificaten (`-days 3650` = 10 jaar voor zelfondertekend). Client secrets verlopen op het schema ingesteld in Entra ID.

---

## Licentie

Dit project valt onder de MIT-licentie — zie [LICENSE](LICENSE) voor de volledige tekst.

Licenties van gebruikte derde componenten staan in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## Auteur

Gebouwd door **Mark Bartelen** bij [QC Tech](https://qctech.nl).
