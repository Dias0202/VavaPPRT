import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { StatCard, AgentRow, AgentAvatar, MatchRow } from "./Stats";
import { Conf, WrBar, ClutchGrid, InsightCard, Loader, Empty } from "./blocks";
import Heatmap from "./Heatmap";

const TABS = [
  ["visao", "Visão"], ["combate", "Combate"], ["economia", "Economia"],
  ["mapas", "Mapas"], ["agentes", "Agentes"], ["armas", "Armas"],
  ["insights", "Insights"], ["heatmap", "Heatmap"],
];

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

  if (loading) return <Loader>Carregando estatísticas…</Loader>;
  if (err) return <div className="error-box">{err}</div>;
  if (!ov || ov.matches === 0)
    return <Empty>Nenhuma partida registrada ainda. O poller grava as próximas competitivas automaticamente.</Empty>;

  return (
    <div className="fade">
      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === "visao" && <Visao ov={ov} riotId={riotId} tagline={tagline} />}
      {tab === "combate" && <Combate ov={ov} />}
      {tab === "economia" && <Economia ov={ov} />}
      {tab === "mapas" && <Lazy fetcher={() => api.maps(riotId, tagline)} render={(d) => <Mapas rows={d} />} />}
      {tab === "agentes" && <Lazy fetcher={() => api.agents(riotId, tagline)} render={(d) => <Agentes rows={d} />} />}
      {tab === "armas" && <Lazy fetcher={() => api.weapons(riotId, tagline)} render={(d) => <Armas data={d} />} />}
      {tab === "insights" && <Lazy fetcher={() => api.insights(riotId, tagline)} render={(d) => <Insights data={d} />} />}
      {tab === "heatmap" && <Heatmap riotId={riotId} tagline={tagline} />}
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

// ---------- Visão ----------
function Visao({ ov, riotId, tagline }) {
  return (
    <>
      <div className="stat-grid" style={{ marginBottom: 24 }}>
        <StatCard label="Partidas" value={ov.matches} />
        <StatCard label="Vitória" value={ov.win_rate} suffix="%" tone={ov.win_rate >= 50 ? "teal" : "red"} />
        <StatCard label="K/D" value={ov.kd} tone={ov.kd >= 1 ? "teal" : "red"} />
        <StatCard label="ACS" value={ov.acs} />
        <StatCard label="ADR" value={ov.adr} />
        <StatCard label="HS%" value={ov.hs_pct} suffix="%" />
        <StatCard label="KAST" value={ov.kast} suffix="%" tone={ov.kast >= 70 ? "teal" : undefined} />
        <StatCard label="V — D" value={`${ov.wins} — ${ov.losses}`} />
      </div>
      <div className="section-title"><span className="eyebrow">Últimas partidas</span></div>
      <Lazy fetcher={() => api.recent(riotId, tagline)} render={(rows) => (
        rows.length ? rows.map((m) => <MatchRow key={m.match_id} m={m} />) : <Empty>Sem partidas.</Empty>
      )} />
    </>
  );
}

// Aviso quando faltam partidas completas (métricas profundas).
function DeepNote({ ov }) {
  if ((ov.deep_matches || 0) >= 3) return null;
  return (
    <div className="error-box" style={{ borderColor: "var(--muted-2)", background: "rgba(126,140,153,0.08)" }}>
      Métricas de combate vêm só de partidas completas ({ov.deep_matches || 0} até agora).
      O histórico de backfill conta para vitória/K-D, mas clutch, entrada e KAST liberam conforme você joga.
    </div>
  );
}

// ---------- Combate ----------
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
            {[["2K", mk["2k"]], ["3K", mk["3k"]], ["4K", mk["4k"]], ["5K", mk["5k"]]].map(([l, v]) => (
              <div className="clutch-chip" key={l}><div className="sz">{l}</div><div className="v">{v}</div></div>
            ))}
          </div>
        </div>
      </section>
    </div>
    </>
  );
}

// ---------- Economia ----------
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

// ---------- Mapas ----------
function Mapas({ rows }) {
  if (!rows.length) return <Empty>Sem dados de mapa.</Empty>;
  return (
    <div className="panel" style={{ padding: 0 }}>
      <table className="board">
        <thead><tr><th style={{ textAlign: "left", paddingLeft: 16 }}>Mapa</th><th>Jogos</th><th>Vitória</th><th>K/D</th><th>ACS</th><th>Atk</th><th>Def</th></tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.map}>
              <td style={{ textAlign: "left", paddingLeft: 16 }} className="who">
                <span className="u">{m.map}</span> <Conf level={m.confidence} n={m.matches} />
              </td>
              <td>{m.matches}</td>
              <td className={m.win_rate >= 50 ? "win" : "loss"}>{m.win_rate}%</td>
              <td>{m.kd}</td><td>{m.acs}</td><td>{m.atk_wr}%</td><td>{m.def_wr}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- Agentes ----------
function Agentes({ rows }) {
  if (!rows.length) return <Empty>Sem dados de agente.</Empty>;
  const max = rows.reduce((m, a) => Math.max(m, a.score_index), 0);
  return <div className="panel">{rows.map((a) => <AgentRow key={a.agent} agent={a} max={max} />)}</div>;
}

// ---------- Armas ----------
function Armas({ data }) {
  if (!data.weapons.length) return <Empty>Sem kills registrados.</Empty>;
  const max = data.weapons[0].kills || 1;
  return (
    <div className="panel">
      {data.weapons.map((w) => (
        <div className="agent-row" key={w.weapon}>
          <div className="agent-name"><strong>{w.weapon}</strong></div>
          <div style={{ textAlign: "right", minWidth: 200 }}>
            <span className="mono">{w.kills} <span className="muted">({w.share}%)</span></span>
            <div className="bar" style={{ marginTop: 6, width: 180, marginLeft: "auto" }}>
              <span style={{ width: `${Math.round((w.kills / max) * 100)}%` }} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------- Insights ----------
function Insights({ data }) {
  if (data.confidence === "low" && data.note)
    return <Empty>{data.note}</Empty>;
  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
      <section>
        <div className="section-title"><span className="eyebrow">Forças</span></div>
        {data.strengths.length ? data.strengths.map((t, i) => <InsightCard key={i} kind="good" text={t} />)
          : <Empty>Nada de destaque ainda.</Empty>}
      </section>
      <section>
        <div className="section-title"><span className="eyebrow">Onde melhorar</span></div>
        {data.gaps.length ? data.gaps.map((t, i) => <InsightCard key={i} kind="gap" text={t} />)
          : <Empty>Sem lacunas evidentes.</Empty>}
      </section>
    </div>
  );
}
