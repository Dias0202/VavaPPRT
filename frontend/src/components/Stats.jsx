// Blocos reutilizáveis de exibição de estatísticas.
import { Link } from "react-router-dom";

export function StatCard({ label, value, suffix, tone }) {
  return (
    <div className="stat">
      <div className="k">{label}</div>
      <div className="v" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
        {suffix && <small> {suffix}</small>}
      </div>
    </div>
  );
}

export function AgentRow({ agent, max }) {
  const pct = max > 0 ? Math.round((agent.score_index / max) * 100) : 0;
  return (
    <div className="agent-row">
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
          <span style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

const ASSET = "https://valorant-api.com/v1/agents?isPlayableCharacter=true";
let _agentMap = null;

export function AgentAvatar({ name, size = 40, cls = "av" }) {
  // Resolve o ícone do agente pela Valorant-API (cacheado no módulo).
  const src = useAgentIcon(name);
  if (!src) return <div className={cls} style={{ width: size, height: size }} />;
  return <img className={cls} style={{ width: size, height: size }} src={src} alt={name} />;
}

import { useEffect, useState } from "react";
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
  return (
    <div className={`match ${m.was_win ? "w" : "l"}`}>
      <div className="edge" />
      <AgentAvatar name={m.agent} size={44} />
      <div className="meta">
        <div className="map">{m.map || "—"}</div>
        <div className="mode">{m.mode || ""}</div>
      </div>
      <div className="kda">
        {m.kills}<span className="muted">/</span>{m.deaths}<span className="muted">/</span>{m.assists}
      </div>
      <div className={`res ${m.was_win ? "win" : "loss"}`}>
        {m.was_win ? "Vitória" : "Derrota"}
      </div>
    </div>
  );
}

export function PlayerLink({ riot_id, tagline, children }) {
  return (
    <Link to={`/jogador/${encodeURIComponent(riot_id)}/${encodeURIComponent(tagline)}`}>
      {children}
    </Link>
  );
}
