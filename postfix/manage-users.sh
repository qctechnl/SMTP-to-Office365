#!/bin/bash
set -e

SASLDB_FILE="/var/lib/sasl2/sasldb2"

usage() {
    cat << 'USAGE'
Usage: manage-users.sh <command> [arguments]

Commands:
  add    <username> <domain>   Add or update a user (prompts for password)
  delete <username> <domain>   Delete a user
  list                         List all users in the database

Examples:
  docker exec -it smtp-to-office365 manage-users.sh add john example.com
  docker exec -it smtp-to-office365 manage-users.sh delete john example.com
  docker exec -it smtp-to-office365 manage-users.sh list
USAGE
}

case "$1" in
    add)
        [[ -z "$2" || -z "$3" ]] && { echo "ERROR: username and domain are required"; usage; exit 1; }
        saslpasswd2 -f "$SASLDB_FILE" -u "$3" -c "$2"
        chown root:postfix "$SASLDB_FILE"
        chmod 640 "$SASLDB_FILE"
        echo "User '$2@$3' added/updated successfully"
        ;;
    delete)
        [[ -z "$2" || -z "$3" ]] && { echo "ERROR: username and domain are required"; usage; exit 1; }
        saslpasswd2 -f "$SASLDB_FILE" -d -u "$3" "$2"
        echo "User '$2@$3' deleted successfully"
        ;;
    list)
        if [[ ! -f "$SASLDB_FILE" ]]; then
            echo "No user database found. Add a user first:"
            echo "  manage-users.sh add <username> <domain>"
            exit 0
        fi
        sasldblistusers2 -f "$SASLDB_FILE"
        ;;
    *)
        usage
        exit 1
        ;;
esac
