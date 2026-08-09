// "Onde filar": para cada mapa da rotação, o veredito do grupo e quem leva
// qual agente. É a decisão que o capitão informal toma antes de abrir a fila —
// e só faz sentido depois que a rotação ativa passou a ser conhecida.
import { motion } from "framer-motion";
import { Empty } from "./blocks";
import { useMapImage } from "../lib/assets";


export default function QueuePlan({ data }) {
  if (!data?.maps?.length)
    return <Empty>Nenhum mapa na rotação. Um admin define em Conta → Rotação de mapas.</Empty>;
  return (
    <div className="map-grid">
      {data.maps.map((m, i) => (
        <MapCard key={m.map} m={m} i={i} />
      ))}
    </div>
  );
}

function MapCard({ m, i }) {
  const img = useMapImage(m.map);
  const best = m.picks?.[0];
  return (
    <motion.article
      className="map-card"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: i * 0.04, duration: 0.25 }}
    >
      {img && <img src={img} alt="" loading="lazy" aria-hidden="true" />}
      <span className={`verdict-tag ${m.verdict.replace(" ", "-")}`}>{m.verdict}</span>
      <div className="name">{m.map}</div>
      <div className="mono muted" style={{ fontSize: 12.5, marginTop: 3 }}>
        {m.basis === "sem amostra"
          ? `${m.basis_games} ${m.basis_games === 1 ? "jogo" : "jogos"} — sem amostra`
          : `${m.basis_win_rate}% em ${m.basis_games} ${m.basis_games === 1 ? "jogo" : "jogos"} ` +
            (m.basis === "grupo" ? "juntos" : "seus")}
      </div>
      {best && (
        <div className="mono muted" style={{ fontSize: 12, marginTop: 5 }}>
          melhor pick: <b style={{ color: "var(--paper)" }}>{best.player} · {best.agent}</b>{" "}
          ({best.win_rate}%)
        </div>
      )}
    </motion.article>
  );
}
