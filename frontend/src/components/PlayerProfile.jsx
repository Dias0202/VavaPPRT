import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { StatCard, AgentRow, MatchRow } from "./Stats";
import { Conf, WrBar, ClutchGrid, InsightCard, Loader, Empty, Gauge } from "./blocks";
import { MIN_MAP } from "../lib/gates";
import Heatmap from "./Heatmap";
import { Tendencia, Sessoes, Rank, PosPlant, Utilidade, Verdict } from "./Progress";

// Percentual que distingue "0%" de "sem amostra" (backfill não tem dado de lado).
const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);

// 13 abas numa fileira só não é navegação, é uma lista. Agrupadas por
// pergunta: como estou indo / o que faço em round / onde e com quem / o que fazer.
const TAB_GROUPS = [
  ["Agora", [["visao", "Visão"], ["tendencia", "Tendência"], ["rank", "Rank"], ["sessoes", "Sessões"]]],
  ["Round", [["combate", "Combate"], ["economia", "Economia"], ["posplant", "Pós-plant"]]],
  ["Contexto", [["mapas", "Mapas"], ["agentes", "Agentes"], ["armas", "Armas"], ["utilidade", "Utilidade"], ["heatmap", "Heatmap"]]],
  ["Ação", [["insights", "Insights"]]],
];

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07, delayChildren: 0.05 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.25 } },
};

export default function PlayerProfile({ riotId, tagline }) {
  const [tab, setTab] = useState("visao");
  const [ov, setOv] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true); setErr(""); setOv(null); setTab("visao");
    api.overview(riotId, tagline)
      .then(setOv).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [riotId, tagline]);

  if (loading) return <ProfileSkeleton />;
  if (err) return <div className="error-box">{err}</div>;
  if (!ov || ov.matches === 0)
    return <Empty>Nenhuma partida registrada ainda. O poller grava as próximas competitivas automaticamente.</Empty>;

  return (
    <motion.div
      className="fade"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <Verdict riotId={riotId} tagline={tagline} ov={ov} />

      <nav className="tabs" aria-label="Seções do perfil">
        {TAB_GROUPS.map(([group, items]) => (
          <div className="tab-group" key={group}>
            <span className="lbl">{group}</span>
            {items.map(([k, label]) => (
              <button
                key={k}
                className={tab === k ? "active" : ""}
                aria-current={tab === k ? "page" : undefined}
                onClick={() => setTab(k)}
              >
                {label}
              </button>
            ))}
          </div>
        ))}
      </nav>
      {tab === "visao" && <Visao ov={ov} riotId={riotId} tagline={tagline} />}
      {tab === "combate" && <Combate ov={ov} />}
      {tab === "economia" && <Economia ov={ov} />}
      {tab === "mapas" && <Lazy fetcher={() => api.maps(riotId, tagline)} render={(d) => <Mapas rows={d} />} />}
      {tab === "agentes" && <Lazy fetcher={() => api.agents(riotId, tagline)} render={(d) => <Agentes rows={d} />} />}
      {tab === "armas" && <Lazy fetcher={() => api.weapons(riotId, tagline)} render={(d) => <Armas data={d} />} />}
      {tab === "tendencia" && <Lazy fetcher={() => api.trend(riotId, tagline)} render={(d) => <Tendencia data={d} />} />}
      {tab === "rank" && <Lazy fetcher={() => api.rank(riotId, tagline)} render={(d) => <Rank data={d} />} />}
      {tab === "sessoes" && <Lazy fetcher={() => api.sessions(riotId, tagline)} render={(d) => <Sessoes data={d} />} />}
      {tab === "posplant" && <Lazy fetcher={() => api.postplant(riotId, tagline)} render={(d) => <PosPlant data={d} />} />}
      {tab === "utilidade" && <Lazy fetcher={() => api.utility(riotId, tagline)} render={(d) => <Utilidade data={d} />} />}
      {tab === "insights" && <Lazy fetcher={() => api.insights(riotId, tagline)} render={(d) => <Insights data={d} />} />}
      {tab === "heatmap" && <Heatmap riotId={riotId} tagline={tagline} />}
    </motion.div>
  );
}

function ProfileSkeleton() {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 14, marginBottom: 24 }}>
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="skeleton skeleton-stat" />
        ))}
      </div>
      <div className="skeleton skeleton-text" style={{ width: "25%", marginBottom: 16 }} />
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 64, marginBottom: 8 }} />
      ))}
    </div>
  );
}

function Lazy({ fetcher, render }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let on = true;
    fetcher().then((d) => on && setData(d)).catch((e) => on && setErr(e.message));
    return () => { on = false; };
  }, []); // eslint-disable-line
  if (err) return <div className="error-box">{err}</div>;
  if (!data) return <Loader />;
  return render(data);
}

function Visao({ ov, riotId, tagline }) {
  const cards = [
    { label: "Partidas", value: ov.matches },
    { label: "Vitória", value: ov.win_rate, suffix: "%", tone: ov.win_rate >= 50 ? "teal" : "red" },
    { label: "K/D", value: ov.kd, tone: ov.kd >= 1 ? "teal" : "red" },
    { label: "ACS", value: ov.acs },
    { label: "ADR", value: ov.adr },
    { label: "HS%", value: ov.hs_pct, suffix: "%" },
    { label: "KAST", value: ov.kast, suffix: "%", tone: ov.kast >= 70 ? "teal" : undefined },
    { label: "V — D", value: `${ov.wins} — ${ov.losses}` },
  ];
  return (
    <>
      <motion.div
        className="stat-grid"
        style={{ marginBottom: 24 }}
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {cards.map((card) => (
          <motion.div key={card.label} variants={itemVariants}>
            <StatCard {...card} />
          </motion.div>
        ))}
      </motion.div>
      <div className="section-title"><span className="eyebrow">Últimas partidas</span></div>
      <Lazy fetcher={() => api.recent(riotId, tagline)} render={(rows) => (
        rows.length
          ? (
            <motion.div variants={containerVariants} initial="hidden" animate="visible">
              {rows.map((m) => (
                <motion.div key={m.match_id} variants={itemVariants}>
                  <MatchRow m={m} />
                </motion.div>
              ))}
            </motion.div>
          )
          : <Empty>Sem partidas.</Empty>
      )} />
    </>
  );
}

function DeepNote({ ov }) {
  if ((ov.deep_matches || 0) >= 3) return null;
  return (
    <div className="error-box" style={{ borderColor: "var(--muted-2)", background: "rgba(126,140,153,0.08)" }}>
      Métricas de combate vêm só de partidas completas ({ov.deep_matches || 0} até agora).
      Clutch, entrada e KAST liberam conforme você joga.
    </div>
  );
}

function Combate({ ov }) {
  const mk = ov.multikills;
  return (
    <>
      <DeepNote ov={ov} />
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <section>
          <div className="section-title"><span className="eyebrow">Entrada (opening duels)</span></div>
          <div className="panel">
            <div className="stat-grid">
              <StatCard label="First Bloods/jogo" value={ov.fb_per_match} tone="teal" />
              <StatCard label="First Deaths/jogo" value={ov.fd_per_match} tone={ov.fd_per_match > ov.fb_per_match ? "red" : undefined} />
            </div>
            <p className="muted" style={{ fontSize: 13, marginTop: 14 }}>
              Quem entra e ganha o 1º duelo dita o round. FD &gt; FB sinaliza entradas mal-calculadas.
            </p>
          </div>
          <div className="section-title" style={{ marginTop: 22 }}><span className="eyebrow">Trade & isolamento</span></div>
          <div className="panel">
            <WrBar label="Trocadas" value={ov.trade_pct} hotBelow={25} />
            <div className="flex between" style={{ marginTop: 10 }}>
              <span className="muted mono" style={{ fontSize: 13 }}>Mortes isoladas (sem trade)</span>
              <span className="mono loss" style={{ fontSize: 18 }}>{ov.deaths_untraded}</span>
            </div>
          </div>
        </section>
        <section>
          <div className="section-title">
            <span className="eyebrow">Clutch</span>
            <Conf level={ov.clutch.attempted >= 5 ? "high" : "low"} n={ov.clutch.attempted} />
          </div>
          <div className="panel">
            <div className="flex between" style={{ marginBottom: 14 }}>
              <span className="muted mono" style={{ fontSize: 13 }}>Convertidos</span>
              <span className="mono" style={{ fontSize: 20 }}>
                {ov.clutch.won}/{ov.clutch.attempted} <span className="muted">({ov.clutch.win_rate}%)</span>
              </span>
            </div>
            <ClutchGrid bySize={ov.clutch.by_size} />
          </div>
          <div className="section-title" style={{ marginTop: 22 }}><span className="eyebrow">Multikills</span></div>
          <div className="panel">
            <div className="clutch-grid" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
              {[["2K", mk["2k"]], ["3K", mk["3k"]], ["4K", mk["4k"]], ["5K", mk["5k"]]].map(([l, v], i) => (
                <motion.div
                  className="clutch-chip"
                  key={l}
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.07, duration: 0.2, ease: [0.34, 1.56, 0.64, 1] }}
                >
                  <div className="sz">{l}</div>
                  <div className="v">{v}</div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

function Economia({ ov }) {
  const e = ov.economy, s = ov.sides;
  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
      <section>
        <div className="section-title"><span className="eyebrow">Win rate por compra</span></div>
        <div className="panel">
          <WrBar label="Pistol" value={e.pistol.wr} n={e.pistol.n} />
          <WrBar label="Eco" value={e.eco.wr} n={e.eco.n} />
          <WrBar label="Força" value={e.force.wr} n={e.force.n} />
          <WrBar label="Full buy" value={e.full.wr} n={e.full.n} />
        </div>
      </section>
      <section>
        <div className="section-title"><span className="eyebrow">Ataque vs Defesa</span></div>
        <div className="panel">
          <WrBar label="Ataque" value={s.attack.wr} n={s.attack.n} />
          <WrBar label="Defesa" value={s.defense.wr} n={s.defense.n} />
          <p className="muted" style={{ fontSize: 13, marginTop: 12 }}>
            Lado é inferido (time que planta = atacante). Diferença grande mostra onde focar treino.
          </p>
        </div>
      </section>
    </div>
  );
}

function Mapas({ rows }) {
  if (!rows.length) return <Empty>Sem dados de mapa.</Empty>;
  return (
    <motion.div className="panel" style={{ padding: 0 }} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <table className="board">
        <thead>
          <tr>
            <th style={{ textAlign: "left", paddingLeft: 16 }}>Mapa</th>
            <th>Jogos</th><th>Vitória</th><th>K/D</th><th>ACS</th><th>Atk</th><th>Def</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m, i) => (
            <motion.tr
              key={m.map}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
            >
              <td style={{ textAlign: "left", paddingLeft: 16 }} className="who">
                <span className="u">{m.map}</span>
                {m.confidence !== "high" && <Conf level={m.confidence} n={m.matches} />}
                <Gauge n={m.matches} floor={MIN_MAP} />
              </td>
              <td>{m.matches}</td>
              <td className={m.win_rate >= 50 ? "win" : "loss"}>{m.win_rate}%</td>
              <td>{m.kd}</td><td>{m.acs}</td><td>{pct(m.atk_wr)}</td><td>{pct(m.def_wr)}</td>
            </motion.tr>
          ))}
        </tbody>
      </table>
    </motion.div>
  );
}

function Agentes({ rows }) {
  if (!rows.length) return <Empty>Sem dados de agente.</Empty>;
  const max = rows.reduce((m, a) => Math.max(m, a.score_index), 0);
  return (
    <motion.div
      className="panel"
      variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.06 } } }}
      initial="hidden"
      animate="visible"
    >
      {rows.map((a) => <AgentRow key={a.agent} agent={a} max={max} />)}
    </motion.div>
  );
}

function Armas({ data }) {
  if (!data.weapons.length) return <Empty>Sem kills registrados.</Empty>;
  const max = data.weapons[0].kills || 1;
  return (
    <motion.div
      className="panel"
      variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.05 } } }}
      initial="hidden"
      animate="visible"
    >
      {data.weapons.map((w, i) => (
        <motion.div
          className="agent-row"
          key={w.weapon}
          variants={{ hidden: { opacity: 0, x: -8 }, visible: { opacity: 1, x: 0 } }}
        >
          <div className="agent-name">
            <strong>{w.weapon}</strong>
            {w.hs_pct !== null && w.hs_pct !== undefined && (
              <span className="muted" style={{ marginLeft: 10, fontSize: 13 }}>
                HS {w.hs_pct}%
                {w.aim_confidence === "low" && (
                  <span title={`Só ${w.aim_rounds} rounds com esta arma`}> ·&nbsp;dados baixos</span>
                )}
              </span>
            )}
          </div>
          <div style={{ textAlign: "right", minWidth: 200 }}>
            <span className="mono">{w.kills} <span className="muted">({w.share}%)</span></span>
            <div className="bar" style={{ marginTop: 6, width: 180, marginLeft: "auto" }}>
              <motion.span
                initial={{ width: 0 }}
                animate={{ width: `${Math.round((w.kills / max) * 100)}%` }}
                transition={{ duration: 0.8, ease: [0.4, 0, 0.2, 1], delay: i * 0.04 }}
              />
            </div>
          </div>
        </motion.div>
      ))}
    </motion.div>
  );
}

function Insights({ data }) {
  if (data.confidence === "low" && data.note)
    return <Empty>{data.note}</Empty>;
  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
      <section>
        <div className="section-title"><span className="eyebrow">Forças</span></div>
        {data.strengths.length
          ? data.strengths.map((t, i) => <InsightCard key={i} kind="good" text={t} />)
          : <Empty>Nada de destaque ainda.</Empty>}
      </section>
      <section>
        <div className="section-title"><span className="eyebrow">Onde melhorar</span></div>
        {data.gaps.length
          ? data.gaps.map((t, i) => <InsightCard key={i} kind="gap" text={t} />)
          : <Empty>Sem lacunas evidentes.</Empty>}
      </section>
    </div>
  );
}
