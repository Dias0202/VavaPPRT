// Blocos pequenos reutilizáveis das telas de analytics.

export function Conf({ level, n }) {
  if (!level) return null;
  const label = level === "high" ? "confiável" : `dados baixos${n != null ? ` · ${n}` : ""}`;
  return <span className={`conf ${level === "high" ? "" : "low"}`}>{label}</span>;
}

export function Eyebrow({ children }) {
  return <span className="eyebrow">{children}</span>;
}

// Barra de win-rate (verde >=50, vermelha <50). Largura proporcional a 0-100.
export function WrBar({ label, value, n, hotBelow = 50 }) {
  const pct = Math.max(0, Math.min(100, value || 0));
  return (
    <div className="wr">
      <span className="lbl">{label}</span>
      <div className="track">
        <span className={pct < hotBelow ? "hot" : ""} style={{ width: `${pct}%` }} />
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
      {sizes.map((s) => {
        const b = bySize?.[s];
        return (
          <div className="clutch-chip" key={s}>
            <div className="sz">1v{s}</div>
            <div className="v">{b ? `${b.won}/${b.att}` : "—"}</div>
          </div>
        );
      })}
    </div>
  );
}

export function InsightCard({ kind, text }) {
  return (
    <div className={`ins ${kind === "good" ? "good" : "gap"}`}>
      <span className="ico">{kind === "good" ? "▲" : "▼"}</span>
      <span>{text}</span>
    </div>
  );
}

export function Loader({ children = "Carregando…" }) {
  return <div className="loader">{children}</div>;
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}
