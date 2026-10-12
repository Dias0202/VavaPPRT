"""Espelho HTTP da botapi (docs/BOT_X9.md §4.1): /bot/v1/tools.

Mesmo contrato que o bot usa importando o módulo. Autenticação por token de
serviço (BOT_API_TOKEN), não por JWT de usuário.
"""
import hmac

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from . import botapi, config

router = APIRouter(prefix="/bot/v1", tags=["bot"])


def service_token(authorization: str = Header(default="")):
    if not config.BOT_API_TOKEN:
        raise HTTPException(503, "BOT_API_TOKEN não configurado no servidor.")
    token = authorization.split(" ", 1)[1].strip() if authorization.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token.encode(), config.BOT_API_TOKEN.encode()):
        raise HTTPException(401, "Token de serviço inválido.")


@router.get("/tools", dependencies=[Depends(service_token)])
def list_tools():
    return botapi.tool_catalog()


@router.post("/tools/{name}", dependencies=[Depends(service_token)])
def run_tool(name: str, body: dict = Body(default={})):
    if name not in botapi.TOOLS:
        raise HTTPException(404, f"Ferramenta '{name}' não existe.")
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(400, "'args' precisa ser um objeto.")
    return botapi.call_tool(name, args, speaker_discord_id=body.get("discord_id"))
