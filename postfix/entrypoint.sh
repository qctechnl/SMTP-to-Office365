#!/bin/bash
set -e

# =============================================================================
# Validate required environment variables
# =============================================================================
required_vars=(
    RELAY_HOSTNAME
    RELAY_ALLOWED_NETWORKS
    RELAY_FROM_ADDRESSES
    ENTRA_TENANT_ID
    ENTRA_CLIENT_ID
    ENTRA_AUTH_TYPE
)

for var in "${required_vars[@]}"; do
    if [[ -z "${!var}" ]]; then
        echo "ERROR: Required environment variable '$var' is not set"
        exit 1
    fi
done

# Validate auth type and check auth-specific variables
case "${ENTRA_AUTH_TYPE}" in
    certificate)
        if [[ -z "${ENTRA_CERT_PATH}" ]]; then
            echo "ERROR: ENTRA_CERT_PATH is required when ENTRA_AUTH_TYPE=certificate"
            exit 1
        fi
        if [[ -z "${ENTRA_KEY_PATH}" ]]; then
            echo "ERROR: ENTRA_KEY_PATH is required when ENTRA_AUTH_TYPE=certificate"
            exit 1
        fi
        if [[ ! -f "${ENTRA_CERT_PATH}" ]]; then
            echo "ERROR: Certificate file not found at ${ENTRA_CERT_PATH}"
            exit 1
        fi
        if [[ ! -f "${ENTRA_KEY_PATH}" ]]; then
            echo "ERROR: Private key file not found at ${ENTRA_KEY_PATH}"
            exit 1
        fi
        ;;
    secret)
        if [[ -z "${ENTRA_CLIENT_SECRET}" ]]; then
            echo "ERROR: ENTRA_CLIENT_SECRET is required when ENTRA_AUTH_TYPE=secret"
            exit 1
        fi
        ;;
    *)
        echo "ERROR: ENTRA_AUTH_TYPE must be 'certificate' or 'secret', got '${ENTRA_AUTH_TYPE}'"
        exit 1
        ;;
esac

# =============================================================================
# Apply environment variables to Postfix configuration
# =============================================================================
postconf -e "myhostname = ${RELAY_HOSTNAME}"
postconf -e "myorigin = ${RELAY_HOSTNAME}"
postconf -e "mynetworks = 127.0.0.0/8 [::1]/128 ${RELAY_ALLOWED_NETWORKS}"

# Inbound TLS level per service (none / may / encrypt)
postconf -e "smtpd_tls_security_level = ${SMTP_TLS_LEVEL:-may}"
postconf -P "587/inet/smtpd_tls_security_level=${SUBMISSION_TLS_LEVEL:-may}"

# Inbound TLS certificate (encrypts SMTP connections from local applications)
postconf -e "smtpd_tls_cert_file = ${SMTP_TLS_CERT_PATH:-/certs/smtp.crt}"
postconf -e "smtpd_tls_key_file = ${SMTP_TLS_KEY_PATH:-/certs/smtp.key}"

# =============================================================================
# Log level
# error   = errors only
# info    = TLS handshake summaries + one line per delivered message (default)
# debug   = full TLS details + Graph API request/response logging
# verbose = same as debug + access token logged on every token fetch (sensitive)
# =============================================================================
case "${LOG_LEVEL:-info}" in
    error)
        postconf -e "smtpd_tls_loglevel = 0"
        ;;
    debug|verbose)
        postconf -e "smtpd_tls_loglevel = 2"
        ;;
    info|*)
        postconf -e "smtpd_tls_loglevel = 1"
        ;;
esac

# =============================================================================
# Build allowed-sender access table from RELAY_FROM_ADDRESSES
# Mail with a MAIL FROM not in this list is rejected at SMTP MAIL FROM time.
# =============================================================================
> /etc/postfix/sasl/allowed_senders
for entry in ${RELAY_FROM_ADDRESSES}; do
    echo "${entry} OK"
done >> /etc/postfix/sasl/allowed_senders
postmap hash:/etc/postfix/sasl/allowed_senders

# =============================================================================
# Configure inbound SASL (Cyrus SASL with sasldb2)
# =============================================================================
SASLDB_FILE="/var/lib/sasl2/sasldb2"

mkdir -p /var/lib/sasl2 /etc/sasl2

cat > /etc/sasl2/smtpd.conf << 'EOF'
pwcheck_method: auxprop
auxprop_plugin: sasldb
mech_list: PLAIN LOGIN
sasldb_path: /var/lib/sasl2/sasldb2
EOF

if [[ -f "${SASLDB_FILE}" ]]; then
    chown root:postfix "${SASLDB_FILE}"
    chmod 640 "${SASLDB_FILE}"
else
    echo "INFO: No sasldb2 found at ${SASLDB_FILE}. Add users with:"
    echo "      docker exec -it postfix-relay manage-users.sh add <username> <domain>"
fi

# =============================================================================
# Start rsyslog (required for Postfix to log in Docker)
# =============================================================================
cat > /etc/rsyslog.conf << 'EOF'
module(load="imuxsock")
mail.*    /var/log/mail.log
EOF

touch /var/log/mail.log
rsyslogd
sleep 1

# =============================================================================
# Fix queue directory permissions (required after fresh volume mount)
# =============================================================================
postfix set-permissions 2>/dev/null || true

# Create token directory for graph-send (pipe transport runs as this user)
TOKEN_DIR=/var/spool/postfix/var/run/graph
mkdir -p "${TOKEN_DIR}"
chown graph-send:graph-send "${TOKEN_DIR}"
chmod 700 "${TOKEN_DIR}"

# When using certificate auth, copy the cert/key into the token directory so
# graph-send can read them (the /certs mount uses host file permissions which
# may not be readable by the graph-send user). Update the env vars so Postfix
# picks up the new paths before starting.
if [[ "${ENTRA_AUTH_TYPE}" == "certificate" ]]; then
    cp "${ENTRA_CERT_PATH}" "${TOKEN_DIR}/entra.crt"
    cp "${ENTRA_KEY_PATH}"  "${TOKEN_DIR}/entra.key"
    chown graph-send:graph-send "${TOKEN_DIR}/entra.crt" "${TOKEN_DIR}/entra.key"
    chmod 644 "${TOKEN_DIR}/entra.crt"
    chmod 600 "${TOKEN_DIR}/entra.key"
    export ENTRA_CERT_PATH="${TOKEN_DIR}/entra.crt"
    export ENTRA_KEY_PATH="${TOKEN_DIR}/entra.key"
fi

# =============================================================================
# Start Postfix
# =============================================================================
echo "Starting Postfix..."
postfix start
echo "Postfix ready. Tailing mail log..."
exec tail -F /var/log/mail.log
