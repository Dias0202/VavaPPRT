// Conta e privacidade. O opt-out cumpre o compromisso do PLANO_ANALYTICS §7:
// apagar puuid e todos os dados associados, a pedido da própria pessoa.
import { useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Account() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const ready = confirm === "APAGAR" && password.length > 0;

  async function submit(e) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setErr("");
    try {
      await api.deleteMe(password);
      logout();
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  }

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
      <span className="eyebrow">Conta</span>
      <h1 style={{ fontSize: 34, margin: "8px 0 22px" }}>
        {user?.username}
      </h1>
      <div className="panel" style={{ marginBottom: 26 }}>
        <p className="muted" style={{ margin: 0 }}>
          Riot ID: <b className="mono">{user?.riot_id}#{user?.tagline}</b> ·
          Região: <b>{user?.region?.toUpperCase()}</b>
        </p>
      </div>

      <h3 className="section-h">Privacidade</h3>
      <p className="muted" style={{ maxWidth: 620, marginBottom: 16 }}>
        Sua análise só é exibida para membros do grupo. Você pode sair a qualquer
        momento: apagamos seu puuid, suas partidas, seus rounds, suas posições no
        mapa, sua curva de RR e suas metas. A ação é <b>irreversível</b> e não tem
        desfazer.
      </p>

      {!open ? (
        <button className="btn ghost" onClick={() => setOpen(true)}>
          Apagar minha conta e meus dados
        </button>
      ) : (
        <form onSubmit={submit} className="panel" style={{ maxWidth: 460 }}>
          {err && <div className="error-box">{err}</div>}
          <div className="field">
            <label>Sua senha</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <div className="field">
            <label>Digite <b className="mono">APAGAR</b> para confirmar</label>
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn" disabled={!ready || busy}>
              {busy ? "Apagando…" : "Confirmar exclusão"}
            </button>
            <button type="button" className="btn ghost" onClick={() => setOpen(false)}>
              Cancelar
            </button>
          </div>
        </form>
      )}
    </motion.div>
  );
}
