"""Anúncio de partidas no Discord — o "placar" do dedo-duro (docs/BOT_X9.md §2.1).

O poller chama `run()` depois de ingerir: cada partida nova do grupo vira um
embed factual (sem LLM) postado num webhook. O claim em `bot_announcements`
garante que poller e bot nunca postem a mesma partida duas vezes; o bot depois
responde na thread dessa mensagem com a zoeira.
"""
import logging
import time

import requests

from . import config, db
from .botapi import _Ctx, _Match, _match_brief

logger = logging.getLogger("announce")

SINCE_KEY = "announce_since"
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6


def announce_since():
    """Início da janela de anúncio. Na primeira vez: agora − lookback."""
    v = db.get_config(SINCE_KEY)
    if v is None:
        v = int(time.time()) - config.ANNOUNCE_LOOKBACK_HOURS * 3600
        db.set_config(SINCE_KEY, v)
    return int(v)


def build_embed(match_id, ctx=None):
    """Embed do Discord (dict) com o placar da partida do ponto de vista do grupo."""
    ctx = ctx or _Ctx()
    brief = _match_brief(ctx, match_id)
    raw = db.get_raw_match(match_id)
    if raw:
        mv = _Match(raw, ctx)
        o, t = mv.final_score()
        won = o > t
        score = f"{o} × {t}"
        color = GREEN if won else RED if o < t else GREY
        result = "VITÓRIA" if won else "DERROTA" if o < t else "EMPATE"
    else:
        won = brief["resultado"] == "vitória"
        score = f"{brief['rounds']} rounds"
        color = GREEN if won else RED
        result = brief["resultado"].upper()

    fields = []
    friends = brief["amigos"]
    for i, f in enumerate(friends):
        tag = " 👑" if i == 0 and len(friends) > 1 else " 💀" if i == len(friends) - 1 and len(friends) > 1 else ""
        fields.append({"name": f"{f['nome']}{tag}",
                       "value": f"{f['agente']} · **{f['kda']}** · ACS {f['acs']} · "
                                f"ADR {f['adr']} · HS {f['hs_pct']}%",
                       "inline": False})
    embed = {
        "title": f"{'🟢' if color == GREEN else '🔴' if color == RED else '⚪'} {result} em "
                 f"{brief['mapa'] or '?'} — {score}",
        "color": color,
        "fields": fields[:10],
        "footer": {"text": "X9 · dedo-duro oficial do grupo"},
    }
    if brief.get("quando"):
        embed["timestamp"] = brief["quando"]["iso"]
    if config.SITE_URL:
        embed["url"] = f"{config.SITE_URL}/partida/{match_id}"
    return embed


def post_webhook(url, embed):
    """POST no webhook (wait=true devolve a mensagem). (channel_id, message_id) ou None."""
    sep = "&" if "?" in url else "?"
    try:
        r = requests.post(f"{url}{sep}wait=true", timeout=20,
                          json={"username": "X9", "embeds": [embed]})
    except requests.RequestException as e:
        logger.warning("Webhook falhou: %s", e)
        return None
    if r.status_code >= 300:
        logger.warning("Webhook HTTP %s: %s", r.status_code, r.text[:300])
        return None
    body = r.json()
    return str(body.get("channel_id")), str(body.get("id"))


def run(webhook_url=None, by="poller"):
    """Anuncia as partidas pendentes. Devolve quantas foram postadas."""
    url = webhook_url if webhook_url is not None else config.DISCORD_WEBHOOK_URL
    if not url:
        return 0
    ctx = _Ctx()
    posted = 0
    for mid in db.unannounced_group_matches(sorted(ctx.friends), announce_since()):
        if not db.claim_announcement(mid, by):
            continue
        try:
            res = post_webhook(url, build_embed(mid, ctx))
        except Exception:
            logger.exception("Embed da partida %s falhou", mid[:8])
            res = None
        if not res:
            db.release_announcement(mid)
            break  # webhook fora do ar: tenta tudo de novo na próxima execução
        db.update_announcement(mid, channel_id=res[0], message_id=res[1])
        posted += 1
    if posted:
        logger.info("Anunciadas %d partida(s) no Discord.", posted)
    return posted
