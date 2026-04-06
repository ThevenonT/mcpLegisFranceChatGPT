from __future__ import annotations

import os
import time
import secrets
from typing import Any, Literal
from datetime import timedelta

import requests
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from mangum import Mangum
from mcp.server.fastmcp import FastMCP

# --- Config PISTE / Legifrance ---
PISTE_CLIENT_ID = os.environ.get("PISTE_CLIENT_ID", "")
PISTE_CLIENT_SECRET = os.environ.get("PISTE_CLIENT_SECRET", "")
PISTE_TOKEN_URL = os.environ.get("PISTE_TOKEN_URL", "https://sandbox-oauth.piste.gouv.fr/api/oauth/token")
LEGIFRANCE_BASE_URL = os.environ.get("LEGIFRANCE_BASE_URL", "").rstrip("/")
LEGIFRANCE_LODA_PATH = os.environ.get("LEGIFRANCE_LODA_PATH", "/consult/loda/search")
LEGIFRANCE_CODE_PATH = os.environ.get("LEGIFRANCE_CODE_PATH", "/consult/code/search")
LEGIFRANCE_JURI_PATH = os.environ.get("LEGIFRANCE_JURI_PATH", "/consult/juri/search")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))

# --- Config OAuth du serveur MCP ---
BASE_URL = os.environ.get("BASE_URL", "https://mcp-legis-france-chat-gpt.vercel.app").rstrip("/")
MCP_CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "chatgpt")
MCP_CLIENT_SECRET = os.environ.get("MCP_CLIENT_SECRET", "")

# Stockage en mémoire des tokens OAuth (valable le temps de la lambda)
_oauth_codes: dict[str, dict] = {}
_oauth_tokens: dict[str, dict] = {}
_piste_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


# --- PISTE token ---
def _get_piste_token() -> str:
    if not PISTE_CLIENT_ID or not PISTE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Missing PISTE credentials")
    now = time.time()
    cached = _piste_token_cache.get("access_token")
    expires_at = float(_piste_token_cache.get("expires_at") or 0)
    if cached and now < expires_at - 30:
        return str(cached)
    try:
        r = requests.post(
            PISTE_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(PISTE_CLIENT_ID, PISTE_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"OAuth PISTE error: {exc}") from exc
    token = payload.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="No access_token from PISTE")
    _piste_token_cache["access_token"] = token
    _piste_token_cache["expires_at"] = now + int(payload.get("expires_in", 3600))
    return str(token)


def _post_legifrance(path: str, payload: dict[str, Any]) -> Any:
    if not LEGIFRANCE_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing LEGIFRANCE_BASE_URL")
    token = _get_piste_token()
    try:
        r = requests.post(
            f"{LEGIFRANCE_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"},
            json={k: v for k, v in payload.items() if v is not None},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        body = getattr(getattr(exc, "response", None), "text", None)
        raise HTTPException(status_code=502, detail=f"Legifrance error: {exc}" + (f" | {body}" if body else "")) from exc
    if "application/json" in r.headers.get("content-type", ""):
        return r.json()
    return r.text


# --- MCP tools ---
mcp = FastMCP("legifrance", json_response=True)

@mcp.tool(name="rechercher_dans_texte_legal", description="Recherche un article ou des mots-clés dans un texte légal français via Legifrance/PISTE.")
def rechercher_dans_texte_legal(search: str, text_id: str | None = None, champ: Literal["ALL", "TITLE", "TABLE", "NUM_ARTICLE", "ARTICLE"] | None = None, type_recherche: Literal["TOUS_LES_MOTS_DANS_UN_CHAMP", "EXPRESSION_EXACTE", "AU_MOINS_UN_MOT"] | None = None, page_size: int | None = 10) -> Any:
    return _post_legifrance(LEGIFRANCE_LODA_PATH, {"search": search, "text_id": text_id, "champ": champ, "type_recherche": type_recherche, "page_size": page_size})

@mcp.tool(name="rechercher_code", description="Recherche des notions, articles ou termes dans un code français via Legifrance/PISTE.")
def rechercher_code(search: str, code_name: str, champ: str | None = None, sort: Literal["PERTINENCE", "DATE_ASC", "DATE_DESC"] | None = None, type_recherche: str | None = None, page_size: int | None = 10, fetch_all: bool | None = False) -> Any:
    return _post_legifrance(LEGIFRANCE_CODE_PATH, {"search": search, "code_name": code_name, "champ": champ, "sort": sort, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all})

@mcp.tool(name="rechercher_jurisprudence_judiciaire", description="Recherche la jurisprudence judiciaire française dans la base JURI de Legifrance via PISTE.")
def rechercher_jurisprudence_judiciaire(search: str, publication_bulletin: list[Literal["T", "F"]] | None = None, sort: Literal["PERTINENCE", "DATE_DESC", "DATE_ASC"] | None = "PERTINENCE", champ: str | None = "ALL", type_recherche: str | None = "TOUS_LES_MOTS_DANS_UN_CHAMP", page_size: int | None = 10, fetch_all: bool | None = False, juri_keys: list[str] | None = None, juridiction_judiciaire: list[str] | None = None) -> Any:
    return _post_legifrance(LEGIFRANCE_JURI_PATH, {"search": search, "publication_bulletin": publication_bulletin, "sort": sort, "champ": champ, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all, "juri_keys": juri_keys, "juridiction_judiciaire": juridiction_judiciaire})


# --- App FastAPI principale ---
app = FastAPI(title="Legifrance MCP", version="1.0.0")

# Monte l'app MCP sous /mcp
mcp_asgi = mcp.streamable_http_app()
app.mount("/mcp", mcp_asgi)


# --- OAuth 2.0 discovery endpoints ---
@app.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource():
    return JSONResponse({
        "resource": BASE_URL,
        "authorization_servers": [BASE_URL],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"]
    })

@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
def oauth_authorization_server():
    return JSONResponse({
        "issuer": BASE_URL,
        "authorization_endpoint": f"{BASE_URL}/oauth/authorize",
        "token_endpoint": f"{BASE_URL}/oauth/token",
        "registration_endpoint": f"{BASE_URL}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mcp"]
    })

@app.get("/.well-known/openid-configuration")
@app.get("/.well-known/openid-configuration/mcp")
def openid_configuration():
    return JSONResponse({
        "issuer": BASE_URL,
        "authorization_endpoint": f"{BASE_URL}/oauth/authorize",
        "token_endpoint": f"{BASE_URL}/oauth/token",
        "response_types_supported": ["code"],
        "scopes_supported": ["mcp", "openid"]
    })


# --- OAuth Dynamic Client Registration ---
@app.post("/oauth/register")
async def oauth_register(request: Request):
    body = await request.json()
    client_id = secrets.token_urlsafe(16)
    client_secret = secrets.token_urlsafe(32)
    return JSONResponse({
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": body.get("client_name", "MCP Client"),
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post"
    }, status_code=201)


# --- OAuth Authorization endpoint ---
@app.get("/oauth/authorize")
async def oauth_authorize(
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "mcp",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
):
    # Page de consentement simple
    html = f"""
    <!DOCTYPE html><html lang="fr">
    <head><meta charset="utf-8"><title>Autorisation Legifrance MCP</title>
    <style>body{{font-family:sans-serif;max-width:400px;margin:80px auto;padding:20px;text-align:center}}
    button{{background:#0070f3;color:#fff;border:none;padding:12px 24px;border-radius:6px;font-size:16px;cursor:pointer;margin:8px}}
    .deny{{background:#e00}}</style></head>
    <body>
    <h2>🇫🇷 Legifrance MCP</h2>
    <p>L'application <strong>{client_id}</strong> demande l'accès au serveur MCP Legifrance.</p>
    <form method="post" action="/oauth/authorize">
        <input type="hidden" name="client_id" value="{client_id}">
        <input type="hidden" name="redirect_uri" value="{redirect_uri}">
        <input type="hidden" name="state" value="{state}">
        <input type="hidden" name="code_challenge" value="{code_challenge}">
        <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
        <input type="hidden" name="scope" value="{scope}">
        <button type="submit" name="action" value="approve">✅ Autoriser</button>
        <button type="submit" name="action" value="deny" class="deny">❌ Refuser</button>
    </form>
    </body></html>
    """
    return HTMLResponse(html)

@app.post("/oauth/authorize")
async def oauth_authorize_post(
    action: str = Form(...),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
    scope: str = Form("mcp"),
):
    if action != "approve":
        return RedirectResponse(f"{redirect_uri}?error=access_denied&state={state}")
    code = secrets.token_urlsafe(32)
    _oauth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires_at": time.time() + 600,
    }
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


# --- OAuth Token endpoint ---
@app.post("/oauth/token")
async def oauth_token(request: Request):
    body = await request.form()
    grant_type = body.get("grant_type", "")

    if grant_type == "authorization_code":
        code = str(body.get("code", ""))
        code_data = _oauth_codes.pop(code, None)
        if not code_data or time.time() > code_data["expires_at"]:
            raise HTTPException(status_code=400, detail="invalid_grant")
        access_token = secrets.token_urlsafe(32)
        _oauth_tokens[access_token] = {
            "scope": code_data["scope"],
            "expires_at": time.time() + 3600,
        }
        return JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 3600,
            "scope": code_data["scope"],
        })

    raise HTTPException(status_code=400, detail="unsupported_grant_type")


# --- Healthcheck ---
@app.get("/")
def healthcheck():
    return {"status": "ok", "mcp": "/mcp", "env_ok": bool(PISTE_CLIENT_ID and PISTE_CLIENT_SECRET and LEGIFRANCE_BASE_URL)}


# --- Vercel handler ---
handler = Mangum(app, lifespan="off")
