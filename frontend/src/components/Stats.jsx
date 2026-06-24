import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";

export function StatCard({ label, value, suffix, tone }) {
  return (
    <motion.div
      className="stat"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
      whileHover={{ y: -3, transition: { duration: 0.12 } }}
    >
      <div className="k">{label}</div>
      <div className="v" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
        {suffix && <small> {suffix}</small>}
      </div>
    </motion.div>
  );
}

export function AgentRow({ agent, max }) {
  const pct = max > 0 ? Math.round((agent.score_index / max) * 100) : 0;
  return (
    <motion.div
      className="agent-row"
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.2 }}
    >
      <div className="agent-name">
        <AgentAvatar name={agent.agent} />
        <div>
          <div style={{ fontWeight: 600 }}>
            {agent.agent}{" "}
            {agent.tag !== "neutro" && (
              <span className={`tag ${agent.tag}`}>{agent.tag}</span>
            )}
          </div>
          <div className="mono muted" style={{ fontSize: 12 }}>
            {agent.matches} partidas · K/D {agent.kd} · ACS {agent.acs}
          </div>
        </div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div className="mono" style={{ fontSize: 15 }}>{agent.win_rate}%</div>
        <div className="bar" style={{ marginTop: 6 }}>
          <motion.span
            initial={{ width: 0 }}
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.9, ease: [0.4, 0, 0.2, 1] }}
          />
        </div>
      </div>
    </motion.div>
  );
}

const ASSET = "https://valorant-api.com/v1/agents?isPlayableCharacter=true";
let _agentMap = null;

export function AgentAvatar({ name, size = 40, cls = "av" }) {
  const src = useAgentIcon(name);
  if (!src) return <div className={cls} style={{ width: size, height: size }} />;
  return (
    <motion.img
      className={cls}
      style={{ width: size, height: size }}
      src={src}
      alt={name}
      initial={{ opacity: 0, scale: 0.85 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    />
  );
}

function useAgentIcon(name) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let alive = true;
    async function resolve() {
      if (!_agentMap) {
        try {
          const r = await fetch(ASSET);
          const j = await r.json();
          _agentMap = {};
          for (const a of j.data || []) _agentMap[a.displayName.toLowerCase()] = a.displayIcon;
        } catch {
          _agentMap = {};
        }
      }
      if (alive) setSrc(_agentMap[(name || "").toLowerCase()] || null);
    }
    resolve();
    return () => { alive = false; };
  }, [name]);
  return src;
}

export function MatchRow({ m }) {
  const won = m.won; // was m.was_win — API returns `won`
  return (
    <motion.div
      className={`match ${won ? "w" : "l"}`}
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.18 }}
      whileHover={{ x: 3, transition: { duration: 0.1 } }}
    >
      <div className="edge" />
      <AgentAvatar name={m.agent} size={44} />
      <div className="meta">
        <div className="map">{m.map || "—"}</div>
        <div className="mode">{m.mode || ""}</div>
      </div>
      <div className="kda">
        {m.kills}<span className="muted">/</span>{m.deaths}<span className="muted">/</span>{m.assists}
        <div className="muted" style={{ fontSize: 11 }}>ACS {m.acs}</div>
      </div>
      <div className={`res ${won ? "win" : "loss"}`}>
        {won ? "Vitória" : "Derrota"}
      </div>
    </motion.div>
  );
}

export function PlayerLink({ riot_id, tagline, children }) {
  return (
    <Link to={`/jogador/${encodeURIComponent(riot_id)}/${encodeURIComponent(tagline)}`}>
      {children}
    </Link>
  );
}
