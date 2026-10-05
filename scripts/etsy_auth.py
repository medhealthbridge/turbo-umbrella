#!/usr/bin/env python3
"""One-time Etsy sign-in. Prints the refresh token to store as ETSY_REFRESH_TOKEN.

    ETSY_API_KEY=<keystring> python scripts/etsy_auth.py

Etsy uses OAuth 2.0 with PKCE. This prints a link; you approve in the browser,
Etsy redirects to your registered redirect URI, and you paste that address back
here (the page it lands on may be an error page — the address is what matters).
Register http://localhost:3003/callback as a redirect URI on your Etsy app, or
pass --redirect with the one you registered. With DATABASE_URL set, the token
is also saved to the database, which is where later runs keep it fresh.
"""

import argparse
import base64
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORIZE = "https://www.etsy.com/oauth/connect"
TOKEN = os.environ.get("ETSY_TOKEN_URL", "https://api.etsy.com/v3/public/oauth/token")
SCOPES = "listings_r listings_w shops_r"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--redirect", default="http://localhost:3003/callback")
    ap.add_argument("--scopes", default=SCOPES)
    args = ap.parse_args()

    key = os.environ.get("ETSY_API_KEY", "").strip()
    if not key:
        sys.exit("Set ETSY_API_KEY to your app's keystring first.")

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    link = AUTHORIZE + "?" + urlencode({
        "response_type": "code", "client_id": key, "redirect_uri": args.redirect,
        "scope": args.scopes, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("1. Open this link while signed in to the Etsy account that owns the shop:\n\n  " + link + "\n")
    pasted = input("2. After you click Allow, paste the full address you land on: ").strip()
    query = parse_qs(urlparse(pasted).query)
    if query.get("state", [""])[0] != state:
        sys.exit("The state in that address doesn't match — start again.")
    if not query.get("code"):
        sys.exit("No code in that address — did you approve the request?")

    resp = requests.post(TOKEN, data={
        "grant_type": "authorization_code", "client_id": key, "redirect_uri": args.redirect,
        "code": query["code"][0], "code_verifier": verifier,
    }, timeout=60)
    if resp.status_code != 200:
        sys.exit(f"Etsy refused the code ({resp.status_code}): {resp.text[:300]}")
    refresh = resp.json()["refresh_token"]

    if os.environ.get("DATABASE_URL"):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from autopost import store
        if store.get().set_credential("etsy_refresh_token", refresh):
            print("Saved to the database.")
    print("\nYour refresh token (keep it secret; it lasts 90 days). Add it as the ETSY_REFRESH_TOKEN secret:\n\n  " + refresh)


if __name__ == "__main__":
    main()
