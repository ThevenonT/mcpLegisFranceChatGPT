from __future__ import annotations

import os
import time
import secrets
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from mangum import Mangum
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PISTE_CLIENT_ID = os.environ.get("PISTE_CLIENT_ID", "")
PISTE_CLIENT_SECRET = os.environ.get("PISTE_CLIENT_SECRET", "")
PISTE_TOKEN_URL = os.environ.get("PISTE_TOKEN_URL", "https://sandbox-oauth.piste.gouv.fr/api/oauth/token")
LEGIFRANCE_BASE_URL = os.environ.get("LEGIFRANCE_BASE_URL", "").rstrip("/")
LEGIFRANCE_LODA_PATH = os.environ.get("LEGIFRANCE_LODA_PATH", "/consult/loda/search")
LEGIFRANCE_CODE_PATH = os.environ.get("LEGIFRANCE_CODE_PATH", "/consult/code/search")
LEGIFRANCE_JURI_PATH = os.environ.get("LEGIFRANCE_JURI_PATH", "/consult/juri/search")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))
BASE_URL = os.environ.get("BASE_URL", "https://mcp-legis-france-chat-gpt.vercel.app").rstrip("/")

# ---------------------------------------------------------------------------
# PISTE token cache
# ---------------------------------------------------------------------------
_piste_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
_oauth_codes: dict[str, dict] = {}
_oauth_tokens: dict[str, dict] = {}


def _get_piste_token() -> str:
    if not PISTE_CLIENT_ID or not PISTE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Missing PISTE credentials")
    now = time.time()
    if _piste_cache["access_token"] and now < float(_piste_cache["expires_at"]) - 30:
        return str(_piste_cache["access_token"])
    try:
        r = requests.post(
            PISTE_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(PISTE_CLIENT_ID, PISTE_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        p = r.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"PISTE OAuth error: {exc}") from exc
    token = p.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="No access_token from PISTE")
    _piste_cache["access_token"] = token
    _piste_cache["expires_at"] = now + int(p.get("expires_in", 3600))
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


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("legifrance", json_response=True)


@mcp.tool(name="rechercher_dans_texte_legal", description="Recherche un article ou des mots-cles dans un texte legal francais via Legifrance/PISTE.")
def rechercher_dans_texte_legal(search: str, text_id: str | None = None, champ: Literal["ALL", "TITLE", "TABLE", "NUM_ARTICLE", "ARTICLE"] | None = None, type_recherche: Literal["TOUS_LES_MOTS_DANS_UN_CHAMP", "EXPRESSION_EXACTE", "AU_MOINS_UN_MOT"] | None = None, page_size: int | None = 10) -> Any:
    return _post_legifrance(LEGIFRANCE_LODA_PATH, {"search": search, "text_id": text_id, "champ": champ, "type_recherche": type_recherche, "page_size": page_size})


@mcp.tool(name="rechercher_code", description="Recherche des notions, articles ou termes dans un code francais via Legifrance/PISTE.")
def rechercher_code(search: str, code_name: str, champ: str | None = None, sort: Literal["PERTINENCE", "DATE_ASC", "DATE_DESC"] | None = None, type_recherche: str | None = None, page_size: int | None = 10, fetch_all: bool | None = False) -> Any:
    return _post_legifrance(LEGIFRANCE_CODE_PATH, {"search": search, "code_name": code_name, "champ": champ, "sort": sort, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all})


@mcp.tool(name="rechercher_jurisprudence_judiciaire", description="Recherche la jurisprudence judiciaire francaise dans la base JURI de Legifrance via PISTE.")
def rechercher_jurisprudence_judiciaire(search: str, publication_bulletin: list[Literal["T", "F"]] | None = None, sort: Literal["PERTINENCE", "DATE_DESC", "DATE_ASC"] | None = "PERTINENCE", champ: str | None = "ALL", type_recherche: str | None = "TOUS_LES_MOTS_DANS_UN_CHAMP", page_size: int | None = 10, fetch_all: bool | None = False, juri_keys: list[str] | None = None, juridiction_judiciaire: list[str] | None = None) -> Any:
    return _post_legifrance(LEGIFRANCE_JURI_PATH, {"search": search, "publication_bulletin": publication_bulletin, "sort": sort, "champ": champ, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all, "juri_keys": juri_keys, "juridiction_judiciaire": juridiction_judiciaire})


# ---------------------------------------------------------------------------
# FastAPI app (OAuth + health)
# ---------------------------------------------------------------------------
fastapi_app = FastAPI(title="Legifrance MCP", version="1.0.0", redirect_slashes=False)


@fastapi_app.get("/")
def health():
    return {"status": "ok", "mcp_endpoint": "/mcp", "env_ok": bool(PISTE_CLIENT_ID and PISTE_CLIENT_SECRET and LEGIFRANCE_BASE_URL), "legifrance_base_url": LEGIFRANCE_BASE_URL or "NOT SET"}


@fastapi_app.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource():
    return JSONResponse({"resource": BASE_URL, "authorization_servers": [BASE_URL], "bearer_methods_supported": ["header"], "scopes_supported": ["mcp"]})


@fastapi_app.get("/.well-known/oauth-authorization-server")
@fastapi_app.get("/.well-known/oauth-authorization-server/mcp")
def oauth_authorization_server():
    return JSONResponse({"issuer": BASE_URL, "authorization_endpoint": f"{BASE_URL}/oauth/authorize", "token_endpoint": f"{BASE_URL}/oauth/token", "registration_endpoint": f"{BASE_URL}/oauth/register", "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"], "scopes_supported": ["mcp"]})


@fastapi_app.get("/.well-known/openid-configuration")
@fastapi_app.get("/.well-known/openid-configuration/mcp")
def openid_configuration():
    return JSONResponse({"issuer": BASE_URL, "authorization_endpoint": f"{BASE_URL}/oauth/authorize", "token_endpoint": f"{BASE_URL}/oauth/token", "response_types_supported": ["code"], "scopes_supported": ["mcp", "openid"]})


@fastapi_app.post("/oauth/register")
async def oauth_register(request: Request):
    body = await request.json()
    return JSONResponse({"client_id": secrets.token_urlsafe(16), "client_secret": secrets.token_urlsafe(32), "client_name": body.get("client_name", "MCP Client"), "redirect_uris": body.get("redirect_uris", []), "grant_types": ["authorization_code"], "response_types": ["code"], "token_endpoint_auth_method": "client_secret_post"}, status_code=201)


@fastapi_app.get("/oauth/authorize")
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


@fastapi_app.post("/oauth/authorize")
async def oauth_authorize_post(action: str = Form(...), client_id: str = Form(""), redirect_uri: str = Form(""), state: str = Form(""), code_challenge: str = Form(""), code_challenge_method: str = Form("S256"), scope: str = Form("mcp")):
    if action != "approve":
        return RedirectResponse(f"{redirect_uri}?error=access_denied&state={state}", status_code=302)
    code = secrets.token_urlsafe(32)
    _oauth_codes[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "code_challenge": code_challenge, "expires_at": time.time() + 600}
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


@fastapi_app.post("/oauth/token")
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


# ---------------------------------------------------------------------------
# ASGI router: /mcp* -> mcp_asgi, tout le reste -> fastapi_app
# Contourne la limitation de Mangum avec app.mount()
# ---------------------------------------------------------------------------
mcp_asgi = mcp.streamable_http_app()


class ASGIRouter:
    """Route /mcp* vers mcp_asgi, le reste vers fastapi_app."""

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path: str = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            # Rewrite path so the mcp app voit "/" ou "/..."
            new_path = path[4:] or "/"  # enleve le prefixe "/mcp"
            scope = {**scope, "path": new_path, "raw_path": new_path.encode()}
            await mcp_asgi(scope, receive, send)
        else:
            await fastapi_app(scope, receive, send)


app = ASGIRouter()

# Vercel handler
handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
