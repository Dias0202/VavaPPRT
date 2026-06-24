import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

const REGIONS = ["latam", "br", "na", "eu", "ap", "kr"];

export default function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({
    username: "", password: "", riot_id: "", tagline: "", region: "latam",
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await register({
        ...f,
        username: f.username.trim(),
        riot_id: f.riot_id.trim(),
        tagline: f.tagline.trim().replace(/^#/, ""),
      });
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
          ENTRA<br />PRO<br /><span className="accent">RANKING.</span>
        </div>
        <p className="muted" style={{ maxWidth: 360 }}>
          Vincule seu Riot ID uma vez. O bot acompanha suas partidas e te coloca
          na tabela do grupo automaticamente.
        </p>
      </aside>

      <section className="auth-form-side">
        <div className="auth-card fade">
          <span className="eyebrow">Cadastro</span>
          <h1 style={{ fontSize: 36, margin: "10px 0 22px" }}>Criar conta</h1>
          {err && <div className="error-box">{err}</div>}
          <form onSubmit={submit}>
            <div className="field">
              <label>Usuário</label>
              <input value={f.username} onChange={set("username")} autoFocus />
            </div>
            <div className="field">
              <label>Senha (mín. 6)</label>
              <input type="password" value={f.password} onChange={set("password")} />
            </div>
            <div className="field">
              <label>Riot ID</label>
              <div className="row">
                <input placeholder="Nome" value={f.riot_id} onChange={set("riot_id")} style={{ flex: 2 }} />
                <input placeholder="Tag" value={f.tagline} onChange={set("tagline")} style={{ flex: 1 }} />
              </div>
            </div>
            <div className="field">
              <label>Região</label>
              <select
                value={f.region}
                onChange={set("region")}
                style={{
                  background: "var(--bg)", border: "1px solid var(--line)",
                  color: "var(--text)", padding: "11px 13px", fontSize: 15,
                }}
              >
                {REGIONS.map((r) => <option key={r} value={r}>{r.toUpperCase()}</option>)}
              </select>
            </div>
            <button className="btn" style={{ width: "100%" }} disabled={busy}>
              {busy ? "Criando…" : "Criar conta"}
            </button>
          </form>
          <p className="muted" style={{ marginTop: 18, fontSize: 14 }}>
            Já tem conta? <Link to="/login" style={{ color: "var(--red)" }}>Entrar</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
