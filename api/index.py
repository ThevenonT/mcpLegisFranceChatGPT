from __future__ import annotations

import os
import time
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from mangum import Mangum

BASE_URL = os.environ.get("BASE_URL", "https://mcp-legis-france-chat-gpt.vercel.app").rstrip("/")
PISTE_CLIENT_ID = os.environ.get("PISTE_CLIENT_ID", "")
PISTE_CLIENT_SECRET = os.environ.get("PISTE_CLIENT_SECRET", "")
LEGIFRANCE_BASE_URL = os.environ.get("LEGIFRANCE_BASE_URL", "")

_oauth_codes: dict[str, dict] = {}
_oauth_tokens: dict[str, dict] = {}

app = FastAPI(title="Legifrance MCP OAuth", redirect_slashes=False)


@app.get("/")
def health():
    return {"status": "ok", "mcp_endpoint": "/mcp", "env_ok": bool(PISTE_CLIENT_ID and PISTE_CLIENT_SECRET and LEGIFRANCE_BASE_URL), "legifrance_base_url": LEGIFRANCE_BASE_URL or "NOT SET"}


@app.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource():
    return JSONResponse({"resource": BASE_URL, "authorization_servers": [BASE_URL], "bearer_methods_supported": ["header"], "scopes_supported": ["mcp"]})


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
def oauth_authorization_server():
    return JSONResponse({"issuer": BASE_URL, "authorization_endpoint": f"{BASE_URL}/oauth/authorize", "token_endpoint": f"{BASE_URL}/oauth/token", "registration_endpoint": f"{BASE_URL}/oauth/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"], "scopes_supported": ["mcp"]})


@app.get("/.well-known/openid-configuration")
@app.get("/.well-known/openid-configuration/mcp")
def openid_configuration():
    return JSONResponse({"issuer": BASE_URL, "authorization_endpoint": f"{BASE_URL}/oauth/authorize", "token_endpoint": f"{BASE_URL}/oauth/token", "response_types_supported": ["code"], "scopes_supported": ["mcp", "openid"]})


@app.post("/oauth/register")
async def oauth_register(request: Request):
    body = await request.json()
    return JSONResponse({"client_id": secrets.token_urlsafe(16), "client_secret": secrets.token_urlsafe(32), "client_name": body.get("client_name", "MCP Client"), "redirect_uris": body.get("redirect_uris", []), "grant_types": ["authorization_code"], "response_types": ["code"], "token_endpoint_auth_method": "client_secret_post"}, status_code=201)


@app.get("/oauth/authorize")
async def oauth_authorize_get(response_type: str = "code", client_id: str = "", redirect_uri: str = "", scope: str = "mcp", state: str = "", code_challenge: str = "", code_challenge_method: str = "S256"):
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Legifrance MCP</title>
    <style>body{{font-family:system-ui,sans-serif;max-width:440px;margin:80px auto;padding:24px;background:#f5f5f5}}.card{{background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 16px rgba(0,0,0,.1);text-align:center}}h2{{margin-bottom:8px}}p{{color:#555;margin-bottom:28px}}button{{padding:12px 28px;border-radius:8px;border:none;font-size:15px;cursor:pointer;margin:6px;font-weight:600}}.allow{{background:#0070f3;color:#fff}}.deny{{background:#dc2626;color:#fff}}</style></head>
    <body><div class="card"><h2>&#127467;&#127479; Legifrance MCP</h2><p><strong>{client_id}</strong><br>demande l'acces au serveur MCP Legifrance.</p>
    <form method="post" action="/oauth/authorize">
    <input type="hidden" name="client_id" value="{client_id}"><input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}"><input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}"><input type="hidden" name="scope" value="{scope}">
    <button class="allow" type="submit" name="action" value="approve">&#10003; Autoriser</button>
    <button class="deny" type="submit" name="action" value="deny">&#10007; Refuser</button>
    </form></div></body></html>"""
    return HTMLResponse(html)


@app.post("/oauth/authorize")
async def oauth_authorize_post(action: str = Form(...), client_id: str = Form(""), redirect_uri: str = Form(""), state: str = Form(""), code_challenge: str = Form(""), code_challenge_method: str = Form("S256"), scope: str = Form("mcp")):
    if action != "approve":
        return RedirectResponse(f"{redirect_uri}?error=access_denied&state={state}", status_code=302)
    code = secrets.token_urlsafe(32)
    _oauth_codes[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "code_challenge": code_challenge, "expires_at": time.time() + 600}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


@app.post("/oauth/token")
async def oauth_token(request: Request):
    body = await request.form()
    if body.get("grant_type") != "authorization_code":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")
    code = str(body.get("code", ""))
    data = _oauth_codes.pop(code, None)
    if not data or time.time() > data["expires_at"]:
        raise HTTPException(status_code=400, detail="invalid_grant")
    token = secrets.token_urlsafe(32)
    _oauth_tokens[token] = {"scope": data["scope"], "expires_at": time.time() + 3600}
    return JSONResponse({"access_token": token, "token_type": "bearer", "expires_in": 3600, "scope": data["scope"]})


handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
