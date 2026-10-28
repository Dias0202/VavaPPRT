"""Anúncio de partidas no Discord — o "placar" do dedo-duro (docs/BOT_X9.md §2.1).

O poller chama `run()` depois de ingerir: cada partida nova do grupo vira um
embed factual (sem LLM) postado num webhook. O claim em `bot_announcements`
garante que poller e bot nunca postem a mesma partida duas vezes; o bot depois
responde na thread dessa mensagem com a zoeira.
"""
import logging
import re
import time

import requests

from . import config, db
from .botapi import _Ctx, _Match, _match_brief, _raw, _row

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


def match_end_raw(match_id):
    """Quando a partida acabou (início + duração da v4), para o cronômetro."""
    raw = _raw(match_id) or {}
    md = raw.get("metadata") or {}
    start = (_row(match_id) or {}).get("started_raw")
    length = md.get("game_length_in_ms")
    if not start:
        return None
    return int(start + (length or 0) / 1000)


def rank_changes(match_id, ctx):
    """{nome: {"rr": +18, "tier": "Gold 3", "subiu": True|False|None}} desta partida."""
    out = {}
    for r in db.rank_rows_for_match(match_id):
        name = ctx.label(r["puuid"])
        if not name:
            continue
        prev = db.previous_rank_row(r["puuid"], r.get("date_raw") or 0)
        moved = None
        if prev and prev.get("tier") and r.get("tier") and prev["tier"] != r["tier"]:
            moved = r["tier"] > prev["tier"]
        out[name] = {"rr": r.get("rr_change"), "tier": r.get("tier_name"), "subiu": moved}
    return out


def build_embed(match_id, ctx=None):
    """Embed do Discord (dict) com o placar da partida do ponto de vista do grupo."""
    ctx = ctx or _Ctx()
    brief = _match_brief(ctx, match_id)
    raw = _raw(match_id)
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
    ranks = rank_changes(match_id, ctx)
    for i, f in enumerate(friends):
        tag = " 👑" if i == 0 and len(friends) > 1 else " 💀" if i == len(friends) - 1 and len(friends) > 1 else ""
        value = (f"{f['agente']} · **{f['kda']}** · ACS {f['acs']} · "
                 f"ADR {f['adr']} · HS {f['hs_pct']}%")
        rk = ranks.get(f["nome"])
        if rk and rk["rr"] is not None:
            value += f" · **{rk['rr']:+d} RR**"
        if rk and rk["subiu"] is not None:
            value += f" · {'⬆️ subiu pra' if rk['subiu'] else '⬇️ caiu pro'} {rk['tier']}"
        fields.append({"name": f"{f['nome']}{tag}", "value": value, "inline": False})
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


_WEBHOOK_TOKEN = re.compile(r"(webhooks/\d+/)[\w.-]+")


def _redact(text):
    """Tira o token do webhook de mensagens de erro: o requests põe a URL
    inteira na exceção, e o log do GitHub Actions deste repo é público."""
    return _WEBHOOK_TOKEN.sub(r"\1***", str(text))


def post_webhook(url, embed):
    """POST no webhook (wait=true devolve a mensagem). (channel_id, message_id) ou None."""
    sep = "&" if "?" in url else "?"
    try:
        r = requests.post(f"{url}{sep}wait=true", timeout=20,
                          json={"username": "X9", "embeds": [embed]})
    except requests.RequestException as e:
        logger.warning("Webhook falhou: %s", _redact(e))
        return None
    if r.status_code >= 300:
        logger.warning("Webhook HTTP %s: %s", r.status_code, _redact(r.text[:300]))
        return None
    body = r.json()
    return str(body.get("channel_id")), str(body.get("id"))


def _webhook_message_url(url, message_id):
    base, _, query = url.partition("?")
    return f"{base.rstrip('/')}/messages/{message_id}" + (f"?{query}" if query else "")


def edit_webhook(url, message_id, embed):
    """Troca o embed de uma mensagem do webhook (ex.: o RR chegou depois)."""
    try:
        r = requests.patch(_webhook_message_url(url, message_id), timeout=20,
                           json={"embeds": [embed]})
    except requests.RequestException as e:
        logger.warning("Edição do webhook falhou: %s", _redact(e))
        return False
    if r.status_code >= 300:
        logger.warning("Edição do webhook HTTP %s: %s", r.status_code, _redact(r.text[:200]))
        return False
    return True


def run(webhook_url=None, by="poller", posted_ids=None):
    """Anuncia as partidas pendentes. Devolve quantas foram postadas.

    posted_ids: lista opcional que recebe (match_id, message_id) do que saiu."""
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
        db.update_announcement(mid, channel_id=res[0], message_id=res[1],
                               ended_raw=match_end_raw(mid), posted_raw=int(time.time()))
        if posted_ids is not None:
            posted_ids.append((mid, res[1]))
        posted += 1
    if posted:
        logger.info("Anunciadas %d partida(s) no Discord.", posted)
    return posted


def refresh_rr(posted_ids, webhook_url=None):
    """Depois do MMR, reescreve o placar das partidas recém-postadas com o RR."""
    url = webhook_url if webhook_url is not None else config.DISCORD_WEBHOOK_URL
    if not url or not posted_ids:
        return 0
    ctx = _Ctx()
    n = 0
    for mid, message_id in posted_ids:
        if not db.rank_rows_for_match(mid):
            continue
        try:
            n += edit_webhook(url, message_id, build_embed(mid, ctx))
        except Exception:
            logger.exception("Não consegui pôr o RR no placar de %s", mid[:8])
    return n
