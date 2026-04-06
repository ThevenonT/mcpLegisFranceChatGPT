from __future__ import annotations

import os
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException
from mcp.server.fastmcp import FastMCP

app = FastAPI(title="Legifrance MCP", version="0.2.0")
mcp = FastMCP("legifrance", json_response=True)

DASSIGNIES_API_KEY = os.getenv("DASSIGNIES_API_KEY")
DASSIGNIES_API_URL = os.getenv("DASSIGNIES_API_URL", "").rstrip("/") + "/"

if not DASSIGNIES_API_KEY or not DASSIGNIES_API_URL.strip("/"):
    raise RuntimeError(
        "Missing required environment variables: DASSIGNIES_API_KEY and DASSIGNIES_API_URL"
    )


def _post(endpoint: str, payload: dict[str, Any]) -> Any:
    try:
        response = requests.post(
            f"{DASSIGNIES_API_URL}{endpoint}",
            params={"api_key": DASSIGNIES_API_KEY},
            headers={"accept": "*/*", "Content-Type": "application/json"},
            json={k: v for k, v in payload.items() if v is not None},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Upstream Legifrance API error: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


@mcp.tool(
    name="rechercher_dans_texte_legal",
    description="Utilise cet outil pour rechercher un article ou des mots-clés dans un texte légal français précis.",
)
def rechercher_dans_texte_legal(
    search: str,
    text_id: str | None = None,
    champ: Literal["ALL", "TITLE", "TABLE", "NUM_ARTICLE", "ARTICLE"] | None = None,
    type_recherche: Literal[
        "TOUS_LES_MOTS_DANS_UN_CHAMP", "EXPRESSION_EXACTE", "AU_MOINS_UN_MOT"
    ] | None = None,
    page_size: int | None = 10,
) -> Any:
    return _post(
        "loda",
        {
            "search": search,
            "text_id": text_id,
            "champ": champ,
            "type_recherche": type_recherche,
            "page_size": page_size,
        },
    )


@mcp.tool(
    name="rechercher_code",
    description="Utilise cet outil pour rechercher des articles ou notions dans un code français, par exemple le Code civil.",
)
def rechercher_code(
    search: str,
    code_name: str,
    champ: str | None = None,
    sort: Literal["PERTINENCE", "DATE_ASC", "DATE_DESC"] | None = None,
    type_recherche: str | None = None,
    page_size: int | None = 10,
    fetch_all: bool | None = False,
) -> Any:
    return _post(
        "code",
        {
            "search": search,
            "code_name": code_name,
            "champ": champ,
            "sort": sort,
            "type_recherche": type_recherche,
            "page_size": page_size,
            "fetch_all": fetch_all,
        },
    )


@mcp.tool(
    name="rechercher_jurisprudence_judiciaire",
    description="Utilise cet outil pour rechercher la jurisprudence judiciaire française pertinente dans la base JURI de Legifrance.",
)
def rechercher_jurisprudence_judiciaire(
    search: str,
    publication_bulletin: list[Literal["T", "F"]] | None = None,
    sort: Literal["PERTINENCE", "DATE_DESC", "DATE_ASC"] | None = "PERTINENCE",
    champ: str | None = "ALL",
    type_recherche: str | None = "TOUS_LES_MOTS_DANS_UN_CHAMP",
    page_size: int | None = 10,
    fetch_all: bool | None = False,
    juri_keys: list[str] | None = None,
    juridiction_judiciaire: list[str] | None = None,
) -> Any:
    return _post(
        "juri",
        {
            "search": search,
            "publication_bulletin": publication_bulletin,
            "sort": sort,
            "champ": champ,
            "type_recherche": type_recherche,
            "page_size": page_size,
            "fetch_all": fetch_all,
            "juri_keys": juri_keys,
            "juridiction_judiciaire": juridiction_judiciaire,
        },
    )


mcp_app = mcp.streamable_http_app()
app.mount("/mcp", mcp_app)


@app.get("/")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "mcp": "/mcp"}
