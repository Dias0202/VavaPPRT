import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await login(username.trim(), password);
      nav("/");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <aside className="auth-hero">
        <span className="eyebrow">Telemetria Valorant · Squad</span>
        <div className="glyph">
          ENTRE<br />NA<br /><span className="accent">PARTIDA.</span>
        </div>
        <p className="muted" style={{ maxWidth: 360 }}>
          K/D, taxa de vitória, agentes fortes e fracos do grupo — atualizados a
          cada rodada, sem você levantar um dedo.
        </p>
      </aside>

      <section className="auth-form-side">
        <div className="auth-card fade">
          <span className="eyebrow">Acesso</span>
          <h1 style={{ fontSize: 36, margin: "10px 0 22px" }}>Login</h1>
          {err && <div className="error-box">{err}</div>}
          <form onSubmit={submit}>
            <div className="field">
              <label>Usuário</label>
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
            </div>
            <div className="field">
              <label>Senha</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <button className="btn" style={{ width: "100%" }} disabled={busy}>
              {busy ? "Entrando…" : "Entrar"}
            </button>
          </form>
          <p className="muted" style={{ marginTop: 18, fontSize: 14 }}>
            Sem conta? <Link to="/registrar" style={{ color: "var(--red)" }}>Cadastre-se</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
