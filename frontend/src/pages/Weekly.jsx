// Relatório semanal do grupo: o que mudou nos últimos 7 dias.
// Roda sobre o dado que o cron já mantém — nenhuma chamada extra à API.
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { Loader, Empty, StaggerList, StaggerItem } from "../components/blocks";
import { StatCard } from "../components/Stats";

const signed = (v) => (v > 0 ? `+${v}` : `${v}`);

export default function Weekly() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.weekly().then(setData).catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="error-box">{err}</div>;
  if (!data) return <Loader />;
  if (!data.players.length)
    return <Empty>Ninguém jogou nos últimos 7 dias — nada a relatar.</Empty>;

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
      <span className="eyebrow">Últimos 7 dias</span>
      <h1 style={{ fontSize: 34, margin: "8px 0 22px" }}>Relatório da semana</h1>

      <div className="stat-grid" style={{ marginBottom: 26 }}>
        <StatCard label={<>Partidas</>} value={<>{data.matches_played}</>} />
        <StatCard
          label="Vitória do grupo" value={data.group_win_rate} suffix="%"
          tone={data.group_win_rate >= 50 ? "teal" : "red"}
        />
        <StatCard label={<>Ativos</>} value={<>{data.active_players}</>} />
        {data.mvp && (
          <StatCard label={<>Destaque</>} value={<>{data.mvp.username}</>} tone="teal" />
        )}
      </div>

      <StaggerList>
        {data.players.map((p) => (
          <StaggerItem key={`${p.riot_id}#${p.tagline}`} className="panel week-row">
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <strong style={{ fontSize: 18 }}>{p.username}</strong>
              <span className="muted mono" style={{ fontSize: 12 }}>
                {p.riot_id}#{p.tagline}
              </span>
              <span style={{ marginLeft: "auto", fontSize: 14 }}>
                {p.matches} jogos · {p.win_rate}% V · K/D {p.kd} · ACS {p.acs}
              </span>
            </div>

            {p.changes.length > 0 ? (
              <div className="week-changes">
                {p.changes.map((c) => (
                  <span key={c.metric} className={`chip ${c.verdict}`}>
                    {c.label} {signed(c.delta)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
                {p.had_previous_week
                  ? "Sem mudanças relevantes vs a semana passada."
                  : "Sem semana anterior para comparar."}
              </p>
            )}

            {p.best_map && (
              <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                Melhor mapa da semana: <b>{p.best_map.map}</b> ({p.best_map.win_rate}% em{" "}
                {p.best_map.matches} jogos)
              </p>
            )}
          </StaggerItem>
        ))}
      </StaggerList>
    </motion.div>
  );
}
