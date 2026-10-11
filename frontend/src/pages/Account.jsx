// Conta e privacidade. O opt-out cumpre o compromisso do PLANO_ANALYTICS §7:
// apagar puuid e todos os dados associados, a pedido da própria pessoa.
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

/**
 * Rotação de mapas — só admin.
 *
 * Ver os 12 mapas do jogo ao mesmo tempo não ajuda a decidir nada: metade não
 * está na fila competitiva. Quem administra o grupo marca o que está no ar, e
 * perfil, insights e plano de fila passam a falar só desses.
 */
export function MapPoolAdmin() {
  const [all, setAll] = useState([]);
  const [pool, setPool] = useState([]);
  const [saved, setSaved] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mapPool()
      .then((d) => { setAll(d.all_maps); setPool(d.pool); setSaved(d.pool); })
      .catch((e) => setErr(e.message));
  }, []);

  const dirty = saved !== null && JSON.stringify([...pool].sort()) !== JSON.stringify([...saved].sort());
  const toggle = (m) =>
    setPool((p) => (p.includes(m) ? p.filter((x) => x !== m) : [...p, m]));

  async function save() {
    setBusy(true);
    setErr("");
    try {
      const d = await api.setMapPool(pool);
      setPool(d.pool);
      setSaved(d.pool);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3 className="section-h">Rotação de mapas · admin</h3>
      {err && <div className="error-box">{err}</div>}
      <p className="muted" style={{ maxWidth: 620, marginBottom: 14 }}>
        Marque os mapas que estão na fila competitiva. As análises de mapa e o
        plano de fila mostram só estes. Sem nenhum marcado, mostramos todos.
      </p>
      <div className="pool-grid" style={{ marginBottom: 16 }}>
        {all.map((m) => (
          <label key={m} className={`pool-item ${pool.includes(m) ? "on" : ""}`}>
            <input type="checkbox" checked={pool.includes(m)} onChange={() => toggle(m)} />
            <span>{m}</span>
          </label>
        ))}
      </div>
      <div className="row">
        <button className="btn" disabled={!dirty || busy} onClick={save}>
          {busy ? "Salvando…" : "Salvar rotação"}
        </button>
        {dirty && <span className="muted" style={{ fontSize: 13 }}>alterações não salvas</span>}
        {!dirty && saved?.length > 0 && (
          <span className="muted" style={{ fontSize: 13 }}>{saved.length} mapas na rotação</span>
        )}
      </div>
    </>
  );
}

function PasswordForm() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      await api.changePassword(cur, next);
      setMsg({ ok: true, text: "Senha alterada." });
      setCur(""); setNext("");
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3 className="section-h">Senha</h3>
      <form onSubmit={submit} className="panel" style={{ maxWidth: 460, marginBottom: 26 }}>
        {msg && <div className={msg.ok ? "ins good" : "error-box"}>{msg.text}</div>}
        <div className="field">
          <label>Senha atual</label>
          <input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
        </div>
        <div className="field">
          <label>Nova senha (mín. 6)</label>
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </div>
        <button className="btn" disabled={busy || !cur || next.length < 6}>
          {busy ? "Salvando…" : "Trocar senha"}
        </button>
      </form>
    </>
  );
}

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

      {user?.is_admin && (
        <p className="muted" style={{ marginBottom: 20 }}>
          Você administra o grupo: aprovações, roster e rotação de mapas ficam em{" "}
          <Link to="/admin" style={{ color: "var(--signal)" }}>Admin</Link>.
        </p>
      )}

      <PasswordForm />

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
