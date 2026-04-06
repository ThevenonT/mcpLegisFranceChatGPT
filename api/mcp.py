from __future__ import annotations

import os
import time
from typing import Any, Literal

import anyio
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from mangum import Mangum
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types

PISTE_CLIENT_ID = os.environ.get("PISTE_CLIENT_ID", "")
PISTE_CLIENT_SECRET = os.environ.get("PISTE_CLIENT_SECRET", "")
PISTE_TOKEN_URL = os.environ.get("PISTE_TOKEN_URL", "https://sandbox-oauth.piste.gouv.fr/api/oauth/token")
LEGIFRANCE_BASE_URL = os.environ.get("LEGIFRANCE_BASE_URL", "").rstrip("/")
LEGIFRANCE_LODA_PATH = os.environ.get("LEGIFRANCE_LODA_PATH", "/consult/loda/search")
LEGIFRANCE_CODE_PATH = os.environ.get("LEGIFRANCE_CODE_PATH", "/consult/code/search")
LEGIFRANCE_JURI_PATH = os.environ.get("LEGIFRANCE_JURI_PATH", "/consult/juri/search")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "30"))

_piste_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


def _get_piste_token() -> str:
    if not PISTE_CLIENT_ID or not PISTE_CLIENT_SECRET:
        raise ValueError("Missing PISTE credentials")
    now = time.time()
    if _piste_cache["access_token"] and now < float(_piste_cache["expires_at"]) - 30:
        return str(_piste_cache["access_token"])
    r = requests.post(
        PISTE_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(PISTE_CLIENT_ID, PISTE_CLIENT_SECRET),
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    p = r.json()
    token = p["access_token"]
    _piste_cache["access_token"] = token
    _piste_cache["expires_at"] = now + int(p.get("expires_in", 3600))
    return str(token)


def _post(path: str, payload: dict[str, Any]) -> Any:
    token = _get_piste_token()
    r = requests.post(
        f"{LEGIFRANCE_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"},
        json={k: v for k, v in payload.items() if v is not None},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json() if "application/json" in r.headers.get("content-type", "") else r.text


# --- MCP Server (low-level API, compatible stateless) ---
server = Server("legifrance")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="rechercher_code",
            description="Recherche des articles ou notions dans un code juridique francais via Legifrance/PISTE.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Termes a rechercher (ex: 'L1221-19' ou 'periode essai')"},
                    "code_name": {"type": "string", "description": "Nom du code (ex: 'Code du travail', 'Code civil')"},
                    "page_size": {"type": "integer", "default": 10},
                    "champ": {"type": "string", "enum": ["ALL", "TITLE", "TABLE", "NUM_ARTICLE", "ARTICLE"], "default": "ALL"},
                },
                "required": ["search", "code_name"],
            },
        ),
        types.Tool(
            name="rechercher_dans_texte_legal",
            description="Recherche dans les textes legislatifs et reglementaires (lois, decrets) via Legifrance/PISTE.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "text_id": {"type": "string", "description": "Identifiant LEGI du texte (optionnel)"},
                    "page_size": {"type": "integer", "default": 10},
                },
                "required": ["search"],
            },
        ),
        types.Tool(
            name="rechercher_jurisprudence_judiciaire",
            description="Recherche dans la jurisprudence judiciaire francaise (base JURI) via Legifrance/PISTE.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "page_size": {"type": "integer", "default": 10},
                    "sort": {"type": "string", "enum": ["PERTINENCE", "DATE_DESC", "DATE_ASC"], "default": "PERTINENCE"},
                },
                "required": ["search"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if not LEGIFRANCE_BASE_URL:
        return [types.TextContent(type="text", text="Erreur: LEGIFRANCE_BASE_URL non configure")]
    try:
        if name == "rechercher_code":
            result = _post(LEGIFRANCE_CODE_PATH, arguments)
        elif name == "rechercher_dans_texte_legal":
            result = _post(LEGIFRANCE_LODA_PATH, arguments)
        elif name == "rechercher_jurisprudence_judiciaire":
            result = _post(LEGIFRANCE_JURI_PATH, arguments)
        else:
            return [types.TextContent(type="text", text=f"Outil inconnu: {name}")]
        import json
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as exc:
        return [types.TextContent(type="text", text=f"Erreur: {exc}")]


# --- FastAPI + SSE transport ---
app = FastAPI(title="Legifrance MCP", redirect_slashes=False)
sse = SseServerTransport("/mcp/messages")


@app.get("/mcp")
async def mcp_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)


@app.get("/")
def health():
    return {"status": "ok", "transport": "sse", "env_ok": bool(PISTE_CLIENT_ID and PISTE_CLIENT_SECRET and LEGIFRANCE_BASE_URL)}


handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
