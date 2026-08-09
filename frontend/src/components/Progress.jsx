// Blocos da dimensão temporal: tendência, sessões/tilt, curva de RR,
// pós-plant e utilidade. O que responde "estou melhorando?".
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Conf, WrBar, Empty, StaggerList, StaggerItem, Gauge } from "./blocks";
import { StatCard } from "./Stats";
import { MIN_SITE, MIN_TREND } from "../lib/gates";
import { api } from "../lib/api";

const fmt = (v, suffix = "") =>
  v === null || v === undefined ? "—" : `${v}${suffix}`;

const signed = (v) => (v > 0 ? `+${v}` : `${v}`);

// ---------------------------------------------------------------------------
// Veredito — o herói do perfil.
// Antes de qualquer número, uma frase: como você está indo e por quê. Sem isso
// o perfil abre com oito cartões e deixa a leitura por conta do usuário.
// ---------------------------------------------------------------------------
export function Verdict({ riotId, tagline, ov }) {
  const [rank, setRank] = useState(null);
  const [trend, setTrend] = useState(null);

  useEffect(() => {
    let on = true;
    api.rank(riotId, tagline).then((d) => on && setRank(d)).catch(() => {});
    api.trend(riotId, tagline).then((d) => on && setTrend(d)).catch(() => {});
    return () => { on = false; };
  }, [riotId, tagline]);

  const rr = rank?.available ? rank.net_rr_last20 : null;
  const worst = trend?.available ? trend.declined?.[0] : null;
  const best = trend?.available ? trend.improved?.[0] : null;

  let dir = "flat";
  let lede;
  if (rr !== null && rank?.verdict !== "estável") {
    dir = rr > 0 ? "up" : "down";
    lede = (
      <>
        Você está <b>{rank.verdict}</b>: {signed(rr)} RR nas últimas{" "}
        {rank.matches_last20} partidas.
      </>
    );
  } else if (rank?.available) {
    lede = (
      <>
        <b>Estável</b> em {rank.current.tier_name} — {signed(rr ?? 0)} RR nas
        últimas {rank.matches_last20}.
      </>
    );
  } else {
    lede = (
      <>
        {ov.matches} partidas registradas, <b>{ov.win_rate}%</b> de vitória.
      </>
    );
  }

  // A justificativa é o que mais mudou, não uma lista.
  let why = null;
  if (worst) {
    why = (
      <>
        O que mais caiu: <b>{worst.label}</b>, de {worst.previous} para{" "}
        {worst.current}.
        {best && <> Em compensação, {best.label.toLowerCase()} subiu para {best.current}.</>}
      </>
    );
  } else if (best) {
    why = <>Melhor movimento: <b>{best.label}</b>, de {best.previous} para {best.current}.</>;
  } else if (trend && !trend.available) {
    why = <>Ainda sem janela anterior cheia para comparar — {trend.reason}.</>;
  }

  return (
    <motion.section
      className={`verdict ${dir}`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
    >
      <p className="lede">{lede}</p>
      {why && <p className="why">{why}</p>}
      {rank?.peak?.tier_name && (
        <p className="why">
          Pico histórico <b>{rank.peak.tier_name}</b>
          {rank.peak.season && ` (${rank.peak.season})`}
          {rank.current_act && (
            <> · melhor deste ato <b>{rank.current_act.peak_tier_name}</b></>
          )}
        </p>
      )}
    </motion.section>
  );
}

// ---------------------------------------------------------------------------
// Tendência
// ---------------------------------------------------------------------------
export function Tendencia({ data }) {
  if (!data?.available) {
    return (
      <Empty>
        {data?.reason
          ? `Ainda não dá pra comparar janelas: ${data.reason}.`
          : "Sem dados de tendência."}
      </Empty>
    );
  }
  return (
    <>
      <p className="muted" style={{ marginBottom: 18 }}>
        Últimas {data.window} partidas comparadas com as {data.window} anteriores.
      </p>
      <StaggerList className="trend-list">
        {data.deltas.map((d) => (
          <StaggerItem key={d.metric} className={`trend-row ${d.verdict}`}>
            <span className="trend-label">{d.label}</span>
            <span className="trend-prev">{d.previous}</span>
            <span className="trend-arrow">→</span>
            <span className="trend-cur">{d.current}</span>
            <span className={`trend-delta ${d.verdict}`}>
              {d.verdict === "estável" ? "=" : signed(d.delta)}
            </span>
          </StaggerItem>
        ))}
      </StaggerList>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sessões e tilt
// ---------------------------------------------------------------------------
export function Sessoes({ data }) {
  if (!data?.sessions?.length) return <Empty>Sem sessões registradas ainda.</Empty>;
  const tilt = data.tilt || {};
  return (
    <>
      {tilt.available && (
        <motion.div
          className={`tilt-box ${tilt.drop >= 12 ? "bad" : tilt.drop >= 5 ? "warn" : "ok"}`}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <strong>{tilt.verdict === "sem queda" ? "Sem tilt detectado" : "Queda ao longo da sessão"}</strong>
          <p>
            Nas 2 primeiras partidas de cada sessão vocês vencem{" "}
            <b>{tilt.early_wr}%</b> ({tilt.early_matches} jogos). Da 4ª em diante,{" "}
            <b>{tilt.late_wr}%</b> ({tilt.late_matches} jogos).
            {tilt.drop >= 5 && ` Queda de ${tilt.drop} pontos — considere parar antes.`}
          </p>
        </motion.div>
      )}

      {data.curve?.length > 0 && (
        <>
          <h3 className="section-h">Rendimento por posição na sessão</h3>
          {data.curve.map((c) => (
            <div key={String(c.position)} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 92, fontSize: 13 }} className="muted">
                {c.position === "5+" ? "5ª+" : `${c.position}ª partida`}
              </span>
              <div style={{ flex: 1 }}>
                <WrBar label="" value={c.win_rate} n={c.matches} />
                <Gauge n={c.matches} floor={MIN_TREND} />
              </div>
              {c.confidence !== "high" && <Conf level={c.confidence} n={c.matches} />}
            </div>
          ))}
        </>
      )}

      <h3 className="section-h">Sessões recentes</h3>
      <StaggerList>
        {data.sessions.map((s, i) => (
          <StaggerItem key={i} className="session-row">
            <span className="muted" style={{ minWidth: 92, fontSize: 13 }}>
              {s.started_raw
                ? new Date(s.started_raw * 1000).toLocaleDateString("pt-BR")
                : "—"}
            </span>
            <span className="session-dots">
              {s.results.map((won, j) => (
                <i key={j} className={won ? "w" : "l"} title={won ? "Vitória" : "Derrota"} />
              ))}
            </span>
            <span style={{ marginLeft: "auto", fontSize: 13 }}>
              {s.wins}V — {s.losses}D · K/D {s.kd}
            </span>
          </StaggerItem>
        ))}
      </StaggerList>
    </>
  );
}

// ---------------------------------------------------------------------------
// Curva de RR
// ---------------------------------------------------------------------------
export function Rank({ data }) {
  if (!data?.available) return <Empty>{data?.note || "Sem histórico de RR."}</Empty>;
  const pts = data.points.filter((p) => p.elo != null);
  return (
    <>
      <div className="stat-grid" style={{ marginBottom: 20 }}>
        <StatCard label="Rank agora" value={data.current.tier_name || "—"} />
        <StatCard label="RR" value={fmt(data.current.rr)} />
        <StatCard
          label={`RR nas últimas ${data.matches_last20}`}
          value={signed(data.net_rr_last20)}
          tone={data.net_rr_last20 >= 0 ? "teal" : "red"}
        />
        {data.current_act && (
          <StatCard
            label={`Pico do ato ${data.current_act.season}`}
            value={data.current_act.peak_tier_name}
          />
        )}
        {data.peak?.tier_name && (
          <StatCard
            label={`Pico histórico${data.peak.season ? ` · ${data.peak.season}` : ""}`}
            value={data.peak.tier_name}
            tone="gold"
          />
        )}
      </div>
      <p className="muted" style={{ marginBottom: 14 }}>
        Tendência: <b>{data.verdict}</b>
      </p>
      {pts.length > 1 && <EloSpark points={pts} />}

      {data.seasons?.length > 1 && (
        <>
          <h3 className="section-h">Por ato</h3>
          <table className="tbl">
            <thead>
              <tr><th>Ato</th><th>Jogos</th><th>Vitória</th><th>Pico</th><th>Terminou</th></tr>
            </thead>
            <tbody>
              {[...data.seasons].reverse().map((s) => (
                <tr key={s.season}>
                  <td className="mono">{s.season}</td>
                  <td>{s.games}</td>
                  <td>{s.win_rate}%</td>
                  <td>{s.peak_tier_name}</td>
                  <td>{s.final_tier_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

// Sparkline da curva de elo em SVG puro — sem dependência nova.
function EloSpark({ points }) {
  const W = 720, H = 160, PAD = 8;
  const elos = points.map((p) => p.elo);
  const min = Math.min(...elos), max = Math.max(...elos);
  const span = max - min || 1;
  const step = points.length > 1 ? (W - PAD * 2) / (points.length - 1) : 0;
  const coords = points.map((p, i) => [
    PAD + i * step,
    PAD + (H - PAD * 2) * (1 - (p.elo - min) / span),
  ]);
  const path = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${path} L${coords[coords.length - 1][0].toFixed(1)},${H - PAD} L${PAD},${H - PAD} Z`;
  const up = elos[elos.length - 1] >= elos[0];
  const stroke = up ? "var(--teal, #35d0b0)" : "var(--red, #ff4655)";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="elo-spark" role="img"
         aria-label={`Curva de elo: ${min} a ${max}`}>
      <defs>
        <linearGradient id="eloFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#eloFill)" />
      <motion.path
        d={path} fill="none" stroke={stroke} strokeWidth="2"
        strokeLinejoin="round" strokeLinecap="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
        transition={{ duration: 0.9, ease: "easeOut" }}
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Pós-plant / retake / sites
// ---------------------------------------------------------------------------
export function PosPlant({ data }) {
  if (!data || data.matches === 0)
    return <Empty>Sem partidas completas o bastante — rode o backfill profundo.</Empty>;
  return (
    <>
      <div className="stat-grid" style={{ marginBottom: 22 }}>
        <StatCard label={<>Plants</>} value={<>{data.plants}</>} />
        <StatCard label={<>Defuses</>} value={<>{data.defuses}</>} />
        <StatCard label={<>Pós-plant</>} value={<>{fmt(data.postplant.win_rate, "%")}</>} />
        <StatCard label={<>Retake</>} value={<>{fmt(data.retake.win_rate, "%")}</>} />
      </div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Pós-plant = rounds em que <b>você plantou</b> ({data.postplant.rounds}).
        Retake = rounds em que <b>o inimigo plantou</b> ({data.retake.rounds}).
      </p>
      {data.sites.length > 0 && (
        <>
          <h3 className="section-h">Por site</h3>
          {data.sites.map((s) => (
            <div key={s.site} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 40, fontWeight: 700 }}>{s.site}</span>
              <div style={{ flex: 1 }}>
                <WrBar label="" value={s.win_rate} n={s.plants} />
                <Gauge n={s.plants} floor={MIN_SITE} />
              </div>
              {s.confidence !== "high" && <Conf level={s.confidence} n={s.plants} />}
            </div>
          ))}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Utilidade
// ---------------------------------------------------------------------------
export function Utilidade({ data }) {
  if (!data || data.matches === 0)
    return <Empty>Sem partidas completas o bastante para medir utilidade.</Empty>;
  const o = data.overall;
  return (
    <>
      <div className="stat-grid" style={{ marginBottom: 22 }}>
        <StatCard label={<>Casts / round</>} value={<>{o.total_per_round}</>} />
        <StatCard label={<>Habilidade 1</>} value={<>{o.ability1}</>} />
        <StatCard label={<>Habilidade 2</>} value={<>{o.ability2}</>} />
        <StatCard label={<>Ultimate</>} value={<>{o.ultimate}</>} />
      </div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Para iniciador e controlador, ACS mede mal — utilidade por round mede o
        trabalho que esses agentes de fato fazem.
      </p>
      <h3 className="section-h">Por agente</h3>
      <table className="tbl">
        <thead>
          <tr><th>Agente</th><th>Jogos</th><th>Casts/round</th><th>Ult/round</th><th></th></tr>
        </thead>
        <tbody>
          {data.by_agent.map((a) => (
            <tr key={a.agent}>
              <td>{a.agent}</td>
              <td>{a.matches}</td>
              <td>{a.total_per_round}</td>
              <td>{a.ultimate}</td>
              <td><Conf level={a.confidence} n={a.matches} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
