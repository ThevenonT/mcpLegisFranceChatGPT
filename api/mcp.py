from __future__ import annotations

import os
import time
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException
from mangum import Mangum
from mcp.server.fastmcp import FastMCP

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
        raise HTTPException(status_code=502, detail=f"PISTE error: {exc}") from exc
    token = p.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="No access_token from PISTE")
    _piste_cache["access_token"] = token
    _piste_cache["expires_at"] = now + int(p.get("expires_in", 3600))
    return str(token)


def _post(path: str, payload: dict[str, Any]) -> Any:
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


# MCP server — exposed directement a la racine de cette fonction
mcp = FastMCP("legifrance", json_response=True)


@mcp.tool(name="rechercher_dans_texte_legal", description="Recherche un article ou des mots-cles dans un texte legal francais via Legifrance/PISTE.")
def rechercher_dans_texte_legal(search: str, text_id: str | None = None, champ: Literal["ALL", "TITLE", "TABLE", "NUM_ARTICLE", "ARTICLE"] | None = None, type_recherche: Literal["TOUS_LES_MOTS_DANS_UN_CHAMP", "EXPRESSION_EXACTE", "AU_MOINS_UN_MOT"] | None = None, page_size: int | None = 10) -> Any:
    return _post(LEGIFRANCE_LODA_PATH, {"search": search, "text_id": text_id, "champ": champ, "type_recherche": type_recherche, "page_size": page_size})


@mcp.tool(name="rechercher_code", description="Recherche des notions, articles ou termes dans un code francais via Legifrance/PISTE.")
def rechercher_code(search: str, code_name: str, champ: str | None = None, sort: Literal["PERTINENCE", "DATE_ASC", "DATE_DESC"] | None = None, type_recherche: str | None = None, page_size: int | None = 10, fetch_all: bool | None = False) -> Any:
    return _post(LEGIFRANCE_CODE_PATH, {"search": search, "code_name": code_name, "champ": champ, "sort": sort, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all})


@mcp.tool(name="rechercher_jurisprudence_judiciaire", description="Recherche la jurisprudence judiciaire francaise dans la base JURI de Legifrance via PISTE.")
def rechercher_jurisprudence_judiciaire(search: str, publication_bulletin: list[Literal["T", "F"]] | None = None, sort: Literal["PERTINENCE", "DATE_DESC", "DATE_ASC"] | None = "PERTINENCE", champ: str | None = "ALL", type_recherche: str | None = "TOUS_LES_MOTS_DANS_UN_CHAMP", page_size: int | None = 10, fetch_all: bool | None = False, juri_keys: list[str] | None = None, juridiction_judiciaire: list[str] | None = None) -> Any:
    return _post(LEGIFRANCE_JURI_PATH, {"search": search, "publication_bulletin": publication_bulletin, "sort": sort, "champ": champ, "type_recherche": type_recherche, "page_size": page_size, "fetch_all": fetch_all, "juri_keys": juri_keys, "juridiction_judiciaire": juridiction_judiciaire})


# L'app MCP est exposee a la racine de /api/mcp -> Vercel la sert sur /mcp
app = mcp.streamable_http_app()
handler = Mangum(app, lifespan="off", api_gateway_base_path="/")
