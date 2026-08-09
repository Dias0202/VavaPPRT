// Metas de treino: fecham o loop do motor de insights.
// O insights aponta a lacuna; aqui ela vira número com acompanhamento.
import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { Loader, Empty, StaggerList, StaggerItem } from "../components/blocks";

export default function Goals() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    api.goals().then(setData).catch((e) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  async function accept(s) {
    setBusy(s.metric);
    setErr("");
    try {
      await api.addGoal({
        metric: s.metric, target: s.target, baseline: s.baseline,
        label: s.label, direction: s.direction,
      });
      load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function remove(id) {
    setBusy(String(id));
    try {
      await api.deleteGoal(id);
      load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  if (err && !data) return <div className="error-box">{err}</div>;
  if (!data) return <Loader />;

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
      <span className="eyebrow">Treino</span>
      <h1 style={{ fontSize: 34, margin: "8px 0 22px" }}>Metas</h1>
      {err && <div className="error-box">{err}</div>}

      <h3 className="section-h">Ativas</h3>
      {data.goals.length === 0 ? (
        <Empty>Nenhuma meta ainda. Aceite uma sugestão abaixo.</Empty>
      ) : (
        <StaggerList>
          {data.goals.map((g) => (
            <StaggerItem key={g.id} className={`panel goal-row ${g.status}`}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <strong style={{ fontSize: 17 }}>{g.label}</strong>
                {g.status === "achieved" && <span className="chip melhorou">batida ✓</span>}
                <span style={{ marginLeft: "auto" }} className="mono">
                  {g.baseline}{g.unit} → <b>{g.target}{g.unit}</b>
                  <span className="muted"> · agora {g.current ?? "—"}{g.unit}</span>
                </span>
                <button
                  className="btn ghost" style={{ padding: "5px 11px" }}
                  disabled={busy === String(g.id)}
                  onClick={() => remove(g.id)}
                >
                  remover
                </button>
              </div>
              <div className="bar" style={{ marginTop: 10 }}>
                <motion.span
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.max(0, Math.min(100, g.progress ?? 0))}%` }}
                  transition={{ duration: 0.7, ease: [0.4, 0, 0.2, 1] }}
                />
              </div>
              <span className="muted" style={{ fontSize: 12 }}>
                {g.progress === null ? "sem medição ainda" : `${g.progress}% do caminho`}
              </span>
            </StaggerItem>
          ))}
        </StaggerList>
      )}

      <h3 className="section-h" style={{ marginTop: 30 }}>Sugeridas</h3>
      {data.suggestions.length === 0 ? (
        <Empty>Sem sugestões no momento — ou você já tem metas para as lacunas atuais.</Empty>
      ) : (
        <StaggerList>
          {data.suggestions.map((s) => (
            <StaggerItem key={s.metric} className="panel goal-suggest">
              <span>{s.text}</span>
              <button
                className="btn" style={{ marginLeft: "auto", padding: "7px 16px" }}
                disabled={busy === s.metric}
                onClick={() => accept(s)}
              >
                {busy === s.metric ? "…" : "adotar"}
              </button>
            </StaggerItem>
          ))}
        </StaggerList>
      )}
    </motion.div>
  );
}
