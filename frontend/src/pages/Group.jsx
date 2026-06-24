import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Conf, WrBar, Loader, Empty } from "../components/blocks";

const REGIONS = ["latam", "br", "na", "eu", "ap", "kr"];

export default function Group() {
  const [squads, setSquads] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null); // grupo selecionado (members)

  function load() {
    api.squads().then(setSquads).catch((e) => setErr(e.message));
  }
  useEffect(load, []);

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">O grupo</span>
        <h1>Grupos & Sinergia</h1>
      </div>

      <AddPlayer onAdded={load} />

      <div className="tac-bar" style={{ margin: "22px 0 18px" }} />
      {err && <div className="error-box">{err}</div>}
      {!squads ? <Loader /> : (
        squads.length === 0
          ? <Empty>Ninguém jogou junto ainda. Adicione amigos acima — quando dois ou mais entrarem na mesma partida e time, o grupo aparece aqui.</Empty>
          : <SquadList squads={squads} onPick={setSel} picked={sel} />
      )}
      {sel && <GroupDetail members={sel} onClose={() => setSel(null)} />}
    </>
  );
}

// ---------- Adicionar pessoa ----------
function AddPlayer({ onAdded }) {
  const [f, setF] = useState({ riot_id: "", tagline: "", region: "na" });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      const r = await api.addPlayer({
        riot_id: f.riot_id.trim(),
        tagline: f.tagline.trim().replace(/^#/, ""),
        region: f.region,
      });
      setMsg({ ok: true, text: r.message || "Adicionado. Baixando dados…" });
      setF({ riot_id: "", tagline: "", region: f.region });
      // dá um tempo pro backfill e recarrega
      setTimeout(onAdded, 8000);
      setTimeout(onAdded, 25000);
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" style={{ background: "var(--surface)" }}>
      <div className="section-title"><span className="eyebrow">Adicionar pessoa ao grupo</span></div>
      <form onSubmit={submit} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <div className="field" style={{ margin: 0, flex: "2 1 180px" }}>
          <label>Riot ID</label>
          <input placeholder="Nome" value={f.riot_id} onChange={set("riot_id")} />
        </div>
        <div className="field" style={{ margin: 0, flex: "1 1 90px" }}>
          <label>Tag</label>
          <input placeholder="0000" value={f.tagline} onChange={set("tagline")} />
        </div>
        <div className="field" style={{ margin: 0, flex: "0 1 110px" }}>
          <label>Região</label>
          <select value={f.region} onChange={set("region")}
            style={{ background: "var(--bg)", border: "1px solid var(--line)", color: "var(--text)", padding: "11px 13px", fontSize: 15 }}>
            {REGIONS.map((r) => <option key={r} value={r}>{r.toUpperCase()}</option>)}
          </select>
        </div>
        <button className="btn" disabled={busy || !f.riot_id || !f.tagline}>
          {busy ? "Adicionando…" : "Adicionar"}
        </button>
      </form>
      {msg && (
        <div className={msg.ok ? "ins good" : "error-box"} style={{ marginTop: 14, marginBottom: 0 }}>
          {msg.ok && <span className="ico">▲</span>}<span>{msg.text}</span>
        </div>
      )}
    </div>
  );
}

// ---------- Lista de grupos ----------
function SquadList({ squads, onPick, picked }) {
  const sameSel = (m) => picked && picked.length === m.length &&
    picked.every((p, i) => p.riot_id === m[i].riot_id && p.tagline === m[i].tagline);

  return (
    <div>
      <div className="section-title"><span className="eyebrow">Grupos que jogam juntos — clique para detalhar</span></div>
      <div className="panel">
        {squads.map((g, i) => {
          const members = g.members.map((m) => ({ riot_id: m.riot_id, tagline: m.tagline }));
          return (
            <div className="duo-row" key={i}
              style={{ cursor: "pointer", background: sameSel(members) ? "var(--surface-2)" : undefined }}
              onClick={() => onPick(members)}>
              <div className="duo-names">
                <span className="tag" style={{ marginRight: 10 }}>{g.size}x</span>
                {g.members.map((m, j) => (
                  <span key={j}>{j > 0 && <span className="x">+</span>}{m.label}</span>
                ))}
                {" "}<Conf level={g.confidence} n={g.games} />
              </div>
              <div className="bar"><span style={{ width: `${g.win_rate}%` }} /></div>
              <div className="mono" style={{ textAlign: "right" }}>
                <span className={g.win_rate >= 50 ? "win" : "loss"}>{g.win_rate}%</span>
                <div className="muted" style={{ fontSize: 11 }}>{g.wins}/{g.games}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatTile({ k, v, sub }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {sub && <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

// ---------- Detalhe do grupo (acionável) ----------
function GroupDetail({ members, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setD(null); setErr("");
    api.groupDetail(members).then(setD).catch((e) => setErr(e.message));
  }, [members]);

  const title = members.map((m) => m.riot_id).join(" + ");
  return (
    <div style={{ marginTop: 28 }} className="fade">
      <div className="flex between" style={{ marginBottom: 14 }}>
        <h2 style={{ fontSize: 24 }}>{title}</h2>
        <button className="btn ghost" style={{ padding: "6px 14px" }} onClick={onClose}>Fechar</button>
      </div>
      {err && <div className="error-box">{err}</div>}
      {!d ? <Loader /> : (
        <>
          <div className="ins good" style={{ borderLeftColor: "var(--red)", background: "rgba(255,70,85,0.06)" }}>
            <span className="ico" style={{ color: "var(--red)" }}>◆</span>
            <span><strong>Recomendação:</strong> {d.recommendation}</span>
          </div>

          {d.form && d.form.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <div className="section-title"><span className="eyebrow">Forma recente (juntos)</span></div>
              <div className="form-row">
                {d.form.map((f, i) => (
                  <div key={i} className={`form-cell ${f.won ? "w" : "l"} ${i === 0 ? "recent" : ""}`} title={f.map || ""}>
                    {f.won ? "V" : "D"}
                  </div>
                ))}
                <span className="muted mono" style={{ fontSize: 11, marginLeft: 8 }}>← mais recente</span>
              </div>
            </div>
          )}

          <div className="stat-grid" style={{ margin: "18px 0" }}>
            <div className="stat">
              <div className="k">Juntos</div>
              <div className="v">{d.together.wins}/{d.together.games}</div>
              <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>
                {d.together.win_rate}% WR <Conf level={d.together.confidence} n={d.together.games} />
              </div>
            </div>
            {d.lifts.map((l, i) => {
              const pos = l.lift >= 0;
              return (
                <div className="stat" key={i} style={{ borderColor: pos ? "var(--teal-dim)" : "var(--red-dim)" }}>
                  <div className="k">Lift de {l.label}</div>
                  <div className="v" style={{ color: `var(--${pos ? "teal" : "red"})` }}>{pos ? "+" : ""}{l.lift}<small> pp</small></div>
                  <div className="muted mono" style={{ fontSize: 11, marginTop: 4 }}>
                    vs {l.apart_wr}% sozinho ({l.apart_games}j)
                  </div>
                </div>
              );
            })}
          </div>

          {d.deep && (
            <div style={{ marginBottom: 8 }}>
              <div className="section-title">
                <span className="eyebrow">Métricas profundas (juntos)</span>
                <Conf level={d.deep.confidence} n={d.deep.games} />
              </div>
              {d.deep.games === 0 ? (
                <div className="panel"><span className="muted">
                  Ainda sem partidas completas jogadas juntas. Clutch, entrada e KAST do grupo
                  liberam conforme vocês acumularem partidas competitivas completas.
                </span></div>
              ) : (
                <div className="stat-grid">
                  <StatTile k="Entrada (FB)" v={d.deep.first_bloods} sub={`${d.deep.first_deaths} first-deaths`} />
                  <StatTile k="KAST do grupo" v={`${d.deep.kast}%`} />
                  <StatTile k="Trade" v={`${d.deep.trade_pct}%`} sub="mortes vingadas" />
                  <StatTile k="Clutch" v={`${d.deep.clutch.won}/${d.deep.clutch.attempted}`} sub={`${d.deep.clutch.win_rate}%`} />
                  <StatTile k="Multikills" v={`${d.deep.multikills["3k"] + d.deep.multikills["4k"] + d.deep.multikills["5k"]}`} sub={`${d.deep.multikills["2k"]}× 2K`} />
                </div>
              )}
            </div>
          )}

          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start", marginTop: 18 }}>
            <section>
              <div className="section-title"><span className="eyebrow">Mapas — o que filar / evitar</span></div>
              <div className="panel">
                {d.maps.length ? d.maps.map((m) => (
                  <div className="wr" key={m.map}>
                    <span className="lbl">{m.map}</span>
                    <div className="track"><span className={m.win_rate < 50 ? "hot" : ""} style={{ width: `${m.win_rate}%` }} /></div>
                    <span className="val">{m.win_rate}% <span className="muted" style={{ fontSize: 11 }}>({m.games})</span></span>
                  </div>
                )) : <Empty>Sem mapas suficientes.</Empty>}
              </div>
            </section>
            <section>
              <div className="section-title"><span className="eyebrow">Melhores composições</span></div>
              <div className="panel">
                {d.comps.length ? d.comps.map((c, i) => (
                  <div className="duo-row" key={i} style={{ gridTemplateColumns: "1fr 70px" }}>
                    <div className="mono" style={{ fontSize: 12 }}>{c.comp}</div>
                    <div className="mono" style={{ textAlign: "right" }}>
                      <span className={c.win_rate >= 50 ? "win" : "loss"}>{c.win_rate}%</span>
                      <div className="muted" style={{ fontSize: 11 }}>{c.games}j</div>
                    </div>
                  </div>
                )) : <Empty>Sem composições.</Empty>}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
