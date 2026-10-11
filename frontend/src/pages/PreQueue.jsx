// Pré-fila: marque quem está on agora e veja, mapa a mapa, se vale aceitar e
// quem leva qual agente. A evidência usada fica explícita: o stack exato vale
// mais que as duplas, que valem mais que o individual.
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useMapImage } from "../lib/assets";
import { AgentAvatar } from "../components/Stats";
import { Conf, Empty, Loader } from "../components/blocks";

const KEY = "vavapprt_prequeue";

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export default function PreQueue() {
  const { user } = useAuth();
  const [roster, setRoster] = useState(null);
  const [sel, setSel] = useState([]);
  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.roster().then((r) => {
      setRoster(r);
      const ids = new Set(r.map((m) => `${m.riot_id}#${m.tagline}`));
      const saved = loadSaved().filter((k) => ids.has(k));
      const me = `${user.riot_id}#${user.tagline}`;
      setSel(saved.length ? saved : ids.has(me) ? [me] : []);
    }).catch((e) => setErr(e.message));
  }, [user]);

  const toggle = (k) => setSel((s) =>
    s.includes(k) ? s.filter((x) => x !== k) : s.length >= 5 ? s : [...s, k]);

  async function run() {
    setBusy(true); setErr(""); setPlan(null);
    try {
      localStorage.setItem(KEY, JSON.stringify(sel));
    } catch { /* sem storage: só não lembra a seleção */ }
    try {
      const members = sel.map((k) => {
        const i = k.lastIndexOf("#");
        return { riot_id: k.slice(0, i), tagline: k.slice(i + 1) };
      });
      setPlan(await api.preQueue(members));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (roster && sel.length && !plan && !busy) run();
  }, [roster]); // eslint-disable-line

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Antes de abrir a fila</span>
        <h1>Quem joga hoje?</h1>
      </div>
      {err && <div className="error-box">{err}</div>}
      {!roster ? <Loader /> : (
        <div className="panel">
          <div className="pool-grid">
            {roster.map((m) => {
              const k = `${m.riot_id}#${m.tagline}`;
              return (
                <label key={k} className={`pool-item ${sel.includes(k) ? "on" : ""}`}>
                  <input type="checkbox" checked={sel.includes(k)} onChange={() => toggle(k)} />
                  <span>{m.label}</span>
                </label>
              );
            })}
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn" disabled={!sel.length || busy} onClick={run}>
              {busy ? "Calculando…" : `Montar plano (${sel.length}/5)`}
            </button>
            <span className="muted" style={{ fontSize: 13 }}>até 5 pessoas — um time</span>
          </div>
        </div>
      )}

      {busy && <Loader>Cruzando o histórico de vocês…</Loader>}
      {plan && <Plan plan={plan} />}
    </>
  );
}

function Plan({ plan }) {
  if (!plan.maps.length) return <Empty>Nenhum mapa na rotação. Um admin define em Admin → Rotação de mapas.</Empty>;
  return (
    <>
      <div className="section-title">
        <span className="eyebrow">Mapas da rotação — do melhor para o pior</span>
      </div>
      <div className="plan-grid">
        {plan.maps.map((m, i) => <PlanCard key={m.map} m={m} i={i} />)}
      </div>
      <p className="muted" style={{ fontSize: 13, marginTop: 14 }}>
        Base da recomendação: <b>stack exato</b> (vocês exatamente assim, juntos) quando há
        jogos suficientes; senão <b>duplas do grupo</b>; senão o <b>individual</b> de cada um.
        A escalação não repete agente e evita duas pessoas na mesma função.
      </p>
    </>
  );
}

function PlanCard({ m, i }) {
  const img = useMapImage(m.map);
  return (
    <motion.article
      className="plan-card"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: i * 0.04, duration: 0.22 }}
    >
      <header>
        {img && <img src={img} alt="" aria-hidden="true" loading="lazy" />}
        <span className={`verdict-tag ${m.verdict.replace(" ", "-")}`}>{m.verdict}</span>
        <div className="name">{m.map}</div>
        <div className="mono muted" style={{ fontSize: 12 }}>
          {m.win_rate === null
            ? `${m.games} jogos — sem amostra`
            : `${m.win_rate}% em ${m.games} · ${m.basis}`}{" "}
          {m.win_rate !== null && <Conf level={m.confidence} n={m.games} />}
        </div>
      </header>
      <ul className="lineup">
        {m.lineup.map((p) => (
          <li key={p.player}>
            {p.agent ? <AgentAvatar name={p.agent} size={28} /> : <span className="av-empty" />}
            <span className="who">{p.player}</span>
            <span className="what">
              {p.agent ? <>{p.agent} <span className="muted">· {p.role}</span></> : <span className="muted">sem histórico</span>}
            </span>
            <span className="mono muted" style={{ fontSize: 11 }}>
              {p.agent ? `${p.win_rate}% (${p.games}j${p.basis === "geral" ? ", geral" : ""})` : ""}
            </span>
          </li>
        ))}
      </ul>
    </motion.article>
  );
}
