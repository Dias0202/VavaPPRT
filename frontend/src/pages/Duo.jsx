// Dupla: o que o tracker.gg não mostra. Juntos vs separados, quem carrega,
// quem vinga quem, onde a dupla rende — e o confronto métrica a métrica.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { api, playerPath } from "../lib/api";
import { Conf, Empty, Loader, WrBar } from "../components/blocks";

const fmt = (v, s = "") => (v === null || v === undefined ? "—" : `${v}${s}`);
const signed = (v) => (v === null || v === undefined ? "—" : v > 0 ? `+${v}` : `${v}`);

export default function Duo() {
  const { ra, ta, rb, tb } = useParams();
  const [d, setD] = useState(null);
  const [h2h, setH2h] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setD(null); setH2h(null); setErr("");
    api.duo(ra, ta, rb, tb).then(setD).catch((e) => setErr(e.message));
    api.headToHead(ra, ta, rb, tb).then(setH2h).catch(() => {});
  }, [ra, ta, rb, tb]);

  if (err) return <div className="error-box">{err}</div>;
  if (!d) return <Loader />;
  const { a, b, together, lift, carry, trades } = d;

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Dupla</span>
        <h1>
          <Link to={playerPath(a)}>{a.label}</Link>
          <span className="muted"> + </span>
          <Link to={playerPath(b)}>{b.label}</Link>
        </h1>
      </div>

      {together.games === 0 ? (
        <Empty>
          {a.label} e {b.label} ainda não jogaram no mesmo time
          {d.against.games ? ` (${d.against.games} partidas em times opostos)` : ""}.
        </Empty>
      ) : (
        <>
          <div className="stat-grid">
            <div className="stat">
              <div className="k">Juntos</div>
              <div className="v">{together.win_rate}<small>%</small></div>
              <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>
                {together.wins}/{together.games} <Conf level={together.confidence} n={together.games} />
              </div>
            </div>
            {["a", "b"].map((k) => (
              <div className="stat" key={k}
                style={{ borderColor: (lift[k].lift ?? 0) >= 0 ? "var(--teal-dim)" : "var(--red-dim)" }}>
                <div className="k">{lift[k].name} com a dupla</div>
                <div className="v" style={{ color: `var(--${(lift[k].lift ?? 0) >= 0 ? "teal" : "red"})` }}>
                  {signed(lift[k].lift)}<small> pp</small>
                </div>
                <div className="muted mono" style={{ fontSize: 11, marginTop: 4 }}>
                  vs {lift[k].apart_wr}% sem o outro ({lift[k].apart_games}j)
                </div>
              </div>
            ))}
          </div>

          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", alignItems: "start", marginTop: 8 }}>
            <section>
              <div className="section-title"><span className="eyebrow">Quem carrega</span></div>
              <div className="panel">
                <CarryBar label={a.label} acs={carry.a_acs} top={carry.a_top} total={together.games} />
                <CarryBar label={b.label} acs={carry.b_acs} top={carry.b_top} total={together.games} />
                <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
                  ACS médio nas partidas juntos e quantas vezes cada um terminou acima do outro.
                </p>
              </div>
            </section>
            <section>
              <div className="section-title">
                <span className="eyebrow">Quem vinga quem</span>
                <Conf level={together.full_games >= 3 ? "high" : "low"} n={together.full_games} />
              </div>
              <div className="panel">
                <WrBar label={`${a.label} vinga ${b.label}`} value={trades.a_avenges_b_pct ?? 0}
                  n={trades.b_deaths} hotBelow={20} />
                <WrBar label={`${b.label} vinga ${a.label}`} value={trades.b_avenges_a_pct ?? 0}
                  n={trades.a_deaths} hotBelow={20} />
                <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
                  % das mortes de um que o outro trocou em até 4 s, matando quem matou.
                  Baixo = vocês jogam longe um do outro.
                </p>
              </div>
            </section>
          </div>

          {d.form.length > 0 && (
            <>
              <div className="section-title"><span className="eyebrow">Forma recente da dupla</span></div>
              <div className="form-row">
                {d.form.map((f, i) => (
                  <Link key={f.match_id} to={`/partida/${f.match_id}`}
                    className={`form-cell ${f.won ? "w" : "l"} ${i === 0 ? "recent" : ""}`} title={f.map || ""}>
                    {f.won ? "V" : "D"}
                  </Link>
                ))}
                <span className="muted mono" style={{ fontSize: 11, marginLeft: 8 }}>← mais recente · clique para abrir</span>
              </div>
            </>
          )}

          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", alignItems: "start" }}>
            <section>
              <div className="section-title"><span className="eyebrow">Mapas da dupla</span></div>
              <div className="panel">
                {d.maps.map((m) => <WrBar key={m.map} label={m.map} value={m.win_rate} n={m.games} />)}
              </div>
            </section>
            <section>
              <div className="section-title"><span className="eyebrow">Combos de agente</span></div>
              <div className="panel">
                {d.agent_combos.map((c) => (
                  <div className="duo-row" key={c.combo} style={{ gridTemplateColumns: "1fr 70px" }}>
                    <div className="mono" style={{ fontSize: 12 }}>{c.combo}</div>
                    <div className="mono" style={{ textAlign: "right" }}>
                      <span className={c.win_rate >= 50 ? "win" : "loss"}>{c.win_rate}%</span>
                      <div className="muted" style={{ fontSize: 11 }}>{c.games}j</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </>
      )}

      {h2h && <HeadToHead h={h2h} />}
    </>
  );
}

function CarryBar({ label, acs, top, total }) {
  const pct = Math.min(100, (acs / 350) * 100);
  return (
    <div className="wr">
      <span className="lbl">{label}</span>
      <div className="track">
        <motion.span initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.7 }} />
      </div>
      <span className="val">
        {acs} <span className="muted" style={{ fontSize: 11 }}>ACS · topo {top}/{total}</span>
      </span>
    </div>
  );
}

function HeadToHead({ h }) {
  return (
    <>
      <div className="section-title">
        <span className="eyebrow">Confronto geral (todas as partidas)</span>
        <span className="mono muted" style={{ fontSize: 12 }}>
          {h.a.name} {h.score.a} × {h.score.b} {h.b.name}
        </span>
      </div>
      <div className="panel" style={{ padding: 0 }}>
        <table className="board">
          <thead>
            <tr>
              <th style={{ textAlign: "left", paddingLeft: 16 }}>Métrica</th>
              <th>{h.a.name}</th><th>{h.b.name}</th>
            </tr>
          </thead>
          <tbody>
            {h.rows.map((r) => (
              <tr key={r.metric}>
                <td style={{ textAlign: "left", paddingLeft: 16 }}>{r.label}</td>
                <td className={r.leader === "a" ? "win" : ""}>{fmt(r.a)}</td>
                <td className={r.leader === "b" ? "win" : ""}>{fmt(r.b)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
