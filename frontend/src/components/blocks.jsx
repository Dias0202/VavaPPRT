import { motion } from "framer-motion";

export function Conf({ level, n }) {
  if (!level) return null;
  const label = level === "high" ? "confiável" : `dados baixos${n != null ? ` · ${n}` : ""}`;
  return <span className={`conf ${level === "high" ? "" : "low"}`}>{label}</span>;
}

/**
 * Régua de amostra — a assinatura visual do produto.
 *
 * O princípio central é não vender ruído como insight. Um selo escrito
 * "dados baixos" diz isso; uma régua deixa VER, e no mesmo lugar em toda a
 * interface: o filete abaixo do número é preenchido na proporção n/piso.
 * Cheio e ciano = amostra suficiente. Curto e cinza = ainda não conclua nada.
 */
export function Gauge({ n, floor, label }) {
  const pct = Math.max(0, Math.min(100, floor > 0 ? (n / floor) * 100 : 0));
  const full = n >= floor;
  return (
    <span
      className={`gauge${full ? " full" : ""}`}
      style={{ "--fill": `${pct}%` }}
      role="img"
      aria-label={
        label ||
        (full
          ? `Amostra suficiente: ${n} de ${floor}`
          : `Amostra insuficiente: ${n} de ${floor}`)
      }
    />
  );
}

export function Eyebrow({ children }) {
  return <span className="eyebrow">{children}</span>;
}

export function WrBar({ label, value, n, hotBelow = 50 }) {
  const pct = Math.max(0, Math.min(100, value || 0));
  return (
    <div className="wr">
      <span className="lbl">{label}</span>
      <div className="track">
        <motion.span
          className={pct < hotBelow ? "hot" : ""}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: [0.4, 0, 0.2, 1], delay: 0.1 }}
        />
      </div>
      <span className="val">
        {value}%{n != null && <span className="muted" style={{ fontSize: 11 }}> ({n})</span>}
      </span>
    </div>
  );
}

export function ClutchGrid({ bySize }) {
  const sizes = ["1", "2", "3", "4", "5"];
  return (
    <div className="clutch-grid">
      {sizes.map((s, i) => {
        const b = bySize?.[s];
        return (
          <motion.div
            className="clutch-chip"
            key={s}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.06, duration: 0.2, ease: [0.34, 1.56, 0.64, 1] }}
          >
            <div className="sz">1v{s}</div>
            <div className="v">{b ? `${b.won}/${b.att}` : "—"}</div>
          </motion.div>
        );
      })}
    </div>
  );
}

export function InsightCard({ kind, text }) {
  return (
    <motion.div
      className={`ins ${kind === "good" ? "good" : "gap"}`}
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.25 }}
    >
      <span className="ico">{kind === "good" ? "▲" : "▼"}</span>
      <span>{text}</span>
    </motion.div>
  );
}

function LoadingDots() {
  return (
    <span style={{ display: "inline-flex", gap: 3, alignItems: "center" }}>
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          style={{
            width: 4, height: 4,
            borderRadius: "50%",
            display: "inline-block",
            background: "var(--muted)",
          }}
          animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1.2, 0.8] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </span>
  );
}

export function Loader({ children = "Carregando…" }) {
  return (
    <motion.div
      className="loader"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 }}
    >
      <LoadingDots />
      {children}
    </motion.div>
  );
}

export function Empty({ children }) {
  return (
    <motion.div
      className="empty"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {children}
    </motion.div>
  );
}

export function StaggerList({ children, className, style }) {
  return (
    <motion.div
      className={className}
      style={style}
      initial="hidden"
      animate="visible"
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: 0.06 } },
      }}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className, style, onClick }) {
  return (
    <motion.div
      className={className}
      style={style}
      onClick={onClick}
      variants={{
        hidden: { opacity: 0, y: 10 },
        visible: { opacity: 1, y: 0, transition: { duration: 0.22 } },
      }}
    >
      {children}
    </motion.div>
  );
}
