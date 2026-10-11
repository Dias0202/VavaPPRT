// Tela de quem se cadastrou e ainda não foi aprovado pelo admin do grupo.
import { useState } from "react";
import { useAuth } from "../lib/auth";

export default function Pending() {
  const { user, refresh, logout } = useAuth();
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    try {
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <aside className="auth-hero">
        <span className="eyebrow">Telemetria Valorant · Squad</span>
        <div className="glyph">
          QUASE<br />LÁ<span className="accent">.</span>
        </div>
      </aside>
      <section className="auth-form-side">
        <div className="auth-card fade">
          <span className="eyebrow">Cadastro recebido</span>
          <h1 style={{ fontSize: 32, margin: "10px 0 16px" }}>Aguardando aprovação</h1>
          <p className="muted">
            O grupo é fechado: quem administra precisa aprovar{" "}
            <b className="mono" style={{ color: "var(--paper)" }}>
              {user?.riot_id}#{user?.tagline}
            </b>{" "}
            antes de você ver os dados. Assim que aprovar, seu histórico começa a
            ser baixado automaticamente.
          </p>
          <div className="row" style={{ marginTop: 22 }}>
            <button className="btn" onClick={check} disabled={busy}>
              {busy ? "Verificando…" : "Já fui aprovado?"}
            </button>
            <button className="btn ghost" onClick={logout}>Sair</button>
          </div>
        </div>
      </section>
    </div>
  );
}
