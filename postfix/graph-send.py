#!/usr/bin/env python3
"""
Postfix pipe transport: delivers mail via Microsoft Graph sendMail API.

Invoked by Postfix as:
  graph-send.py -f <sender> -- <recipient> [<recipient> ...]

Exit codes (Postfix pipe transport / sysexits.h convention):
  0  = delivered
  65 = permanent data error (bounce)
  67 = permanent no-user error (bounce)
  75 = temporary failure (Postfix retries)
  77 = permanent permission denied (bounce)
"""
import argparse
import base64
import email.parser
import email.policy
import email.utils
import json
import math
import msal
import os
import sys
import time
from typing import Optional

import requests

GRAPH_BASE             = "https://graph.microsoft.com/v1.0"
TOKEN_FILE             = "/var/spool/postfix/var/run/graph/token.json"
LARGE_ATTACH_THRESHOLD = 3 * 1024 * 1024  # 3 MB raw; base64 inflates ~37% → stays under Graph's ~4 MB request limit
CHUNK_SIZE             = 4 * 320 * 1024   # 4 × 320 KiB per chunk (Graph requires multiples of 320 KiB)

EX_OK       = 0
EX_DATAERR  = 65
EX_NOUSER   = 67
EX_TEMPFAIL = 75
EX_NOPERM   = 77
_EX_REAUTH  = -1   # sentinel: 401 → refresh token and retry once, then map to EX_TEMPFAIL

log_level                = (os.environ.get("LOG_LEVEL") or "info").lower()
save_sent                = os.environ.get("GRAPH_SAVE_TO_SENT_ITEMS", "false").lower() == "true"
large_attachments_enabled = os.environ.get("GRAPH_LARGE_ATTACHMENTS", "true").lower() == "true"


# =============================================================================
# Logging
# =============================================================================

def _log(msg: str) -> None:
    print(f"graph-send: {msg}", file=sys.stderr)


def log_info(msg: str) -> None:
    if log_level != "error":
        _log(msg)


def log_debug(msg: str) -> None:
    if log_level in ("debug", "verbose"):
        _log(msg)


def log_error(msg: str) -> None:
    _log(f"ERROR: {msg}")


# =============================================================================
# Token handling
# =============================================================================

def _build_msal_app() -> Optional[msal.ConfidentialClientApplication]:
    client_id = os.environ["OAUTH2_CLIENT_ID"]
    authority = f"https://login.microsoftonline.com/{os.environ['OAUTH2_TENANT_ID']}"
    auth_type = os.environ["OAUTH2_AUTH_TYPE"]
    if auth_type == "certificate":
        with open(os.environ["OAUTH2_CERT_PATH"]) as fh:
            cert_data = fh.read()
        with open(os.environ["OAUTH2_KEY_PATH"]) as fh:
            key_data = fh.read()
        credential = {"private_key": key_data, "thumbprint": None, "public_certificate": cert_data}
    elif auth_type == "secret":
        credential = os.environ["OAUTH2_CLIENT_SECRET"]
    else:
        log_error(f"unknown OAUTH2_AUTH_TYPE: {auth_type}")
        return None
    return msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=credential)


def _refresh_token() -> Optional[str]:
    app = _build_msal_app()
    if app is None:
        return None
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        log_error(f"token acquisition failed: {result.get('error_description', result)}")
        return None
    token = result["access_token"]
    expires_in = result.get("expires_in", 3600)
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"access_token": token, "expiry": str(int(time.time()) + expires_in)}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)
    log_debug("token refreshed")
    if log_level == "verbose":
        _log("WARNING: verbose logging enabled — access token follows")
        _log(f"access_token: {token}")
    return token


def read_token() -> Optional[str]:
    try:
        with open(TOKEN_FILE) as fh:
            data = json.load(fh)
        if time.time() > int(data.get("expiry", 0)) - 60:
            log_debug("token near expiry, refreshing")
            return _refresh_token()
        return data["access_token"]
    except FileNotFoundError:
        log_debug("no cached token, fetching from Entra ID")
        return _refresh_token()
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        log_error(f"cannot read token: {exc}")
        return None


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# =============================================================================
# HTTP helpers
# =============================================================================

def _map_status(status: int, body: str) -> int:
    if status == 401:
        return _EX_REAUTH
    if status == 403:
        log_error(f"403 Forbidden — check Application Access Policy membership: {body[:300]}")
        return EX_NOPERM
    if status == 404:
        log_error(f"404 Not Found — mailbox or resource does not exist: {body[:300]}")
        return EX_NOUSER
    if status == 429 or status >= 500:
        log_error(f"transient error {status}: {body[:200]}")
        return EX_TEMPFAIL
    log_error(f"permanent error {status}: {body[:300]}")
    return EX_DATAERR


def _post(url: str, token: str, payload: Optional[dict] = None, timeout: int = 60) -> requests.Response:
    resp = requests.post(url, headers=_auth_header(token), json=payload, timeout=timeout)
    log_debug(f"POST {url} → {resp.status_code}")
    return resp


def _delete_draft(token: str, sender: str, message_id: str) -> None:
    try:
        url = f"{GRAPH_BASE}/users/{sender}/messages/{message_id}"
        requests.delete(url, headers=_auth_header(token), timeout=15)
        log_debug(f"deleted failed draft {message_id}")
    except Exception as exc:
        log_debug(f"draft cleanup failed: {exc}")


# =============================================================================
# Message parsing
# =============================================================================

def _addresses_from_headers(msg, *header_names: str) -> set[str]:
    values: list[str] = []
    for name in header_names:
        values.extend(msg.get_all(name) or [])
    return {addr.lower() for _, addr in email.utils.getaddresses(values) if addr}


def _build_recipients(envelope: list[str], msg) -> tuple[list, list, list]:
    header_to = _addresses_from_headers(msg, "To")
    header_cc = _addresses_from_headers(msg, "Cc")
    to_list, cc_list, bcc_list = [], [], []
    for addr in envelope:
        obj = {"emailAddress": {"address": addr}}
        if addr.lower() in header_to:
            to_list.append(obj)
        elif addr.lower() in header_cc:
            cc_list.append(obj)
        else:
            bcc_list.append(obj)
    return to_list, cc_list, bcc_list


def _extract_body(msg) -> tuple[str, str]:
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return "", "Text"
    content_type_graph = "HTML" if part.get_content_type() == "text/html" else "Text"
    return part.get_content(), content_type_graph


def _extract_attachments(msg) -> list[dict]:
    result = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        result.append({
            "filename":     part.get_filename() or "attachment",
            "content_type": part.get_content_type() or "application/octet-stream",
            "data":         payload,
        })
    return result


# =============================================================================
# Graph send paths
# =============================================================================

def _send_simple(token: str, sender: str, subject: str, body: str, body_type: str,
                 to_list: list, cc_list: list, bcc_list: list,
                 attachments: list[dict]) -> int:
    payload: dict = {
        "message": {
            "subject": subject,
            "body": {"contentType": body_type, "content": body},
            "toRecipients":  to_list,
            "ccRecipients":  cc_list,
            "bccRecipients": bcc_list,
        },
        "saveToSentItems": str(save_sent).lower(),
    }
    if attachments:
        payload["message"]["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name":         a["filename"],
                "contentType":  a["content_type"],
                "contentBytes": base64.b64encode(a["data"]).decode(),
            }
            for a in attachments
        ]

    resp = _post(f"{GRAPH_BASE}/users/{sender}/sendMail", token, payload)
    if 200 <= resp.status_code < 300:
        n = len(to_list) + len(cc_list) + len(bcc_list)
        log_info(f"delivered (simple path) from={sender} recipients={n}")
        return EX_OK
    return _map_status(resp.status_code, resp.text)


def _upload_chunks(upload_url: str, data: bytes) -> int:
    total = len(data)
    num_chunks = math.ceil(total / CHUNK_SIZE)
    for i in range(num_chunks):
        start = i * CHUNK_SIZE
        end   = min(start + CHUNK_SIZE, total) - 1
        chunk = data[start:end + 1]
        headers = {
            "Content-Length": str(len(chunk)),
            "Content-Range":  f"bytes {start}-{end}/{total}",
        }
        resp = requests.put(upload_url, headers=headers, data=chunk, timeout=120)
        is_last = (i == num_chunks - 1)
        log_debug(f"PUT chunk {i+1}/{num_chunks} bytes {start}-{end}/{total} → {resp.status_code}")
        expected = (200, 201) if is_last else (202,)
        if resp.status_code not in expected:
            return _map_status(resp.status_code, resp.text)
    return EX_OK


def _send_large(token: str, sender: str, subject: str, body: str, body_type: str,
                to_list: list, cc_list: list, bcc_list: list,
                attachments: list[dict]) -> int:
    small_atts = [a for a in attachments if len(a["data"]) < LARGE_ATTACH_THRESHOLD]
    large_atts = [a for a in attachments if len(a["data"]) >= LARGE_ATTACH_THRESHOLD]

    draft_payload: dict = {
        "subject": subject,
        "body": {"contentType": body_type, "content": body},
        "toRecipients":  to_list,
        "ccRecipients":  cc_list,
        "bccRecipients": bcc_list,
        "singleValueExtendedProperties": [
            {"id": "Boolean 0x10F4", "value": str(save_sent).lower()},
        ],
    }
    if small_atts:
        draft_payload["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name":         a["filename"],
                "contentType":  a["content_type"],
                "contentBytes": base64.b64encode(a["data"]).decode(),
            }
            for a in small_atts
        ]

    resp = _post(f"{GRAPH_BASE}/users/{sender}/messages", token, draft_payload)
    if resp.status_code not in (200, 201):
        return _map_status(resp.status_code, resp.text)

    message_id = resp.json().get("id")
    if not message_id:
        log_error("no message id in draft-create response")
        return EX_TEMPFAIL

    for att in large_atts:
        session_url = f"{GRAPH_BASE}/users/{sender}/messages/{message_id}/attachments/createUploadSession"
        session_payload = {
            "AttachmentItem": {
                "attachmentType": "file",
                "name":           att["filename"],
                "size":           len(att["data"]),
            }
        }
        resp = _post(session_url, token, session_payload, timeout=30)
        if resp.status_code not in (200, 201):
            _delete_draft(token, sender, message_id)
            return _map_status(resp.status_code, resp.text)

        upload_url = resp.json().get("uploadUrl")
        if not upload_url:
            log_error("no uploadUrl in createUploadSession response")
            _delete_draft(token, sender, message_id)
            return EX_TEMPFAIL

        rc = _upload_chunks(upload_url, att["data"])
        if rc != EX_OK:
            _delete_draft(token, sender, message_id)
            return rc

    send_url = f"{GRAPH_BASE}/users/{sender}/messages/{message_id}/send"
    resp = _post(send_url, token, timeout=60)
    if 200 <= resp.status_code < 300:
        n = len(to_list) + len(cc_list) + len(bcc_list)
        log_info(f"delivered (large-attachment path) from={sender} recipients={n} large_attachments={len(large_atts)}")
        return EX_OK

    rc = _map_status(resp.status_code, resp.text)
    _delete_draft(token, sender, message_id)
    return rc


# =============================================================================
# Entry point
# =============================================================================

def deliver(sender: str, recipients: list[str], raw: bytes, token: str) -> int:
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)

    subject            = msg.get("Subject", "")
    body, body_type    = _extract_body(msg)
    to_list, cc_list, bcc_list = _build_recipients(recipients, msg)
    attachments        = _extract_attachments(msg)
    total_attach_bytes = sum(len(a["data"]) for a in attachments)

    log_debug(f"sender={sender} envelope_recipients={len(recipients)} "
              f"attachments={len(attachments)} attach_bytes={total_attach_bytes}")

    if large_attachments_enabled and total_attach_bytes >= LARGE_ATTACH_THRESHOLD:
        return _send_large(token, sender, subject, body, body_type,
                           to_list, cc_list, bcc_list, attachments)
    else:
        return _send_simple(token, sender, subject, body, body_type,
                            to_list, cc_list, bcc_list, attachments)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", dest="sender", required=True)
    parser.add_argument("recipients", nargs="+")
    args = parser.parse_args()

    raw = sys.stdin.buffer.read()

    token = read_token()
    if token is None:
        sys.exit(EX_TEMPFAIL)

    rc = deliver(args.sender, args.recipients, raw, token)

    if rc == _EX_REAUTH:
        log_debug("received 401, refreshing token and retrying once")
        token = _refresh_token()
        if token is None:
            sys.exit(EX_TEMPFAIL)
        rc = deliver(args.sender, args.recipients, raw, token)
        if rc == _EX_REAUTH:
            log_error("still receiving 401 after token refresh")
            rc = EX_TEMPFAIL

    sys.exit(rc)


if __name__ == "__main__":
    main()
