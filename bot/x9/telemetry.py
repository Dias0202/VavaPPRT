"""Cronômetro do X9 (docs/PLANO_X9_V2.md, Fase 0): quanto cada resposta,
cada fala e cada partida demoram, para o `/x9 status` mostrar p50/p95 reais.

O tempo das partidas (fim → placar → fofoca) fica no banco
(`bot_announcements`); aqui ficam só as amostras do processo atual."""
import time
from collections import deque


def percentile(values, q):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


class Telemetry:
    def __init__(self, maxlen=200, clock=time.time):
        self.clock = clock
        self.answers = deque(maxlen=maxlen)   # (ts, rota, ms, passos, provedor, tools)
        self.voice = deque(maxlen=maxlen)     # (ts, ms do fim da fala ao 1º áudio)
        self.gossip = deque(maxlen=maxlen)    # (ts, s do placar à fofoca)

    def record_answer(self, route, ms, stats=None):
        st = stats or {}
        self.answers.append((self.clock(), route, ms, st.get("steps"), st.get("provider"),
                             tuple(st.get("tools") or ())))

    def record_voice(self, ms):
        self.voice.append((self.clock(), ms))

    def record_gossip(self, seconds):
        self.gossip.append((self.clock(), seconds))

    def answer_summary(self, route):
        rows = [a for a in self.answers if a[1] == route]
        if not rows:
            return None
        ms = [a[2] for a in rows]
        return {"n": len(rows), "p50_s": round(percentile(ms, 0.5) / 1000, 1),
                "p95_s": round(percentile(ms, 0.95) / 1000, 1),
                "sem_tool_pct": round(100 * sum(1 for a in rows if not a[5]) / len(rows)),
                "provedores": sorted({a[4] for a in rows if a[4]})}

    def voice_summary(self):
        if not self.voice:
            return None
        ms = [v[1] for v in self.voice]
        return {"n": len(ms), "p50_s": round(percentile(ms, 0.5) / 1000, 1),
                "p95_s": round(percentile(ms, 0.95) / 1000, 1)}


def match_summary(rows):
    """rows: db.announcement_timings() — fim → placar e placar → fofoca."""
    to_post = [r["posted_raw"] - r["ended_raw"] for r in rows
               if r.get("posted_raw") is not None and r.get("ended_raw") is not None]
    to_gossip = [r["gossip_raw"] - r["posted_raw"] for r in rows
                 if (r.get("gossip_raw") or 0) > 0 and r.get("posted_raw") is not None]
    if not to_post:
        return None
    return {"n": len(to_post),
            "placar_p50_min": round(percentile(to_post, 0.5) / 60, 1),
            "placar_p95_min": round(percentile(to_post, 0.95) / 60, 1),
            "fofoca_p50_s": round(percentile(to_gossip, 0.5)) if to_gossip else None,
            "fofoca_p95_s": round(percentile(to_gossip, 0.95)) if to_gossip else None,
            "pelo_bot_pct": round(100 * sum(1 for r in rows if r.get("claimed_by") == "bot")
                                  / len(rows))}
