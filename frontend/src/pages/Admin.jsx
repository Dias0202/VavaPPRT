// Administração do grupo: aprovar cadastros, cuidar do roster, ver se o
// poller está vivo e definir a rotação de mapas.
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, dateTime } from "../lib/time";
import { Empty, Loader } from "../components/blocks";
import { MapPoolAdmin } from "./Account";

export default function Admin() {
  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Administração</span>
        <h1>Grupo</h1>
      </div>
      <Approvals />
      <Roster />
      <IngestRuns />
      <MapPoolAdmin />
    </>
  );
}

function Approvals() {
  const [users, setUsers] = useState(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(null);

  const load = () => api.adminUsers().then(setUsers).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function act(fn, id, okText) {
    setBusy(id); setErr(""); setMsg("");
    try {
      await fn(id);
      setMsg(okText);
      await load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  }

  const pending = users?.filter((u) => u.status === "pending") || [];
  const members = users?.filter((u) => u.status === "approved") || [];

  return (
    <>
      <h3 className="section-h">Cadastros aguardando aprovação</h3>
      {err && <div className="error-box">{err}</div>}
      {msg && <div className="ins good"><span className="ico">▲</span><span>{msg}</span></div>}
      {!users ? <Loader /> : pending.length === 0 ? (
        <Empty>Ninguém esperando. Quem se cadastrar aparece aqui.</Empty>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          {pending.map((u) => (
            <motion.div key={u.id} className="admin-row" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <div>
                <b>{u.username}</b>{" "}
                <span className="mono muted">{u.riot_id}#{u.tagline}</span>
              </div>
              <span className="mono muted" style={{ fontSize: 12 }}>{u.region?.toUpperCase()}</span>
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn" disabled={busy === u.id}
                  onClick={() => act(api.approveUser, u.id, `${u.username} aprovado — histórico baixando.`)}>
                  Aprovar
                </button>
                <button className="btn ghost" disabled={busy === u.id}
                  onClick={() => act(api.rejectUser, u.id, `${u.username} recusado.`)}>
                  Recusar
                </button>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {members.length > 0 && (
        <>
          <h3 className="section-h">Membros com login</h3>
          <div className="panel" style={{ padding: 0 }}>
            {members.map((u) => (
              <div key={u.id} className="admin-row">
                <div>
                  <b>{u.username}</b>{" "}
                  <span className="mono muted">{u.riot_id}#{u.tagline}</span>
                </div>
                <span className="tag">{u.is_admin ? "admin" : "membro"}</span>
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <AdminToggle u={u} busy={busy === u.id}
                    onToggle={() => act((id) => api.setAdmin(id, !u.is_admin), u.id,
                      u.is_admin ? `${u.username} não é mais admin.` : `${u.username} agora é admin.`)} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function AdminToggle({ u, busy, onToggle }) {
  const { user } = useAuth();
  if (u.id === user?.id) return <span className="muted" style={{ fontSize: 13 }}>você</span>;
  return (
    <button className="btn ghost" disabled={busy} onClick={onToggle}>
      {u.is_admin ? "Tirar admin" : "Tornar admin"}
    </button>
  );
}

function Roster() {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);

  const load = () => api.ingestStatus().then(setSt).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function remove(p) {
    setErr("");
    try {
      await api.removeTracked(p.riot_id, p.tagline);
      setConfirm(null);
      load();
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <>
      <h3 className="section-h">Roster e coleta de dados</h3>
      {err && <div className="error-box">{err}</div>}
      {!st ? <Loader /> : (
        <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
          <table className="board">
            <thead>
              <tr>
                <th style={{ textAlign: "left", paddingLeft: 16 }}>Jogador</th>
                <th>Partidas</th><th>Completas</th><th>Última coleta</th><th>Última partida</th><th />
              </tr>
            </thead>
            <tbody>
              {st.players.map((p) => (
                <tr key={`${p.riot_id}#${p.tagline}`}>
                  <td style={{ textAlign: "left", paddingLeft: 16 }}>
                    <b>{p.label}</b> <span className="mono muted">#{p.tagline}</span>
                    {p.kind === "tracked" && <span className="tag" style={{ marginLeft: 8 }}>rastreado</span>}
                    {p.last_error && (
                      <div className="loss mono" style={{ fontSize: 11 }} title={p.last_error}>
                        erro {ago(p.last_error_raw, st.now_raw)}: {p.last_error.slice(0, 60)}
                      </div>
                    )}
                  </td>
                  <td>{p.matches}</td>
                  <td>{p.coverage_pct == null ? "—" : `${p.coverage_pct}%`}</td>
                  <td>{p.downloading ? "baixando…" : ago(p.last_ingested_raw, st.now_raw)}</td>
                  <td>{ago(p.last_match_raw, st.now_raw)}</td>
                  <td>
                    {p.kind === "tracked" && (confirm === p.riot_id + p.tagline ? (
                      <span className="row" style={{ justifyContent: "flex-end" }}>
                        <button className="btn" onClick={() => remove(p)}>Apagar dados</button>
                        <button className="btn ghost" onClick={() => setConfirm(null)}>Cancelar</button>
                      </span>
                    ) : (
                      <button className="btn ghost" onClick={() => setConfirm(p.riot_id + p.tagline)}>
                        Remover
                      </button>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
        "Completas" é a parte do histórico com rounds e kills (clutch, KAST, trade,
        heatmap). O poller promove algumas partidas de resumo por execução até
        chegar a 100%. Quem tem login só sai pela própria conta.
      </p>
    </>
  );
}

function IngestRuns() {
  const [st, setSt] = useState(null);
  useEffect(() => { api.ingestStatus().then(setSt).catch(() => {}); }, []);
  if (!st) return null;
  return (
    <>
      <h3 className="section-h">Execuções do poller</h3>
      {st.runs.length === 0 ? (
        <Empty>
          O poller ainda não rodou. Ele roda pelo GitHub Actions a cada ~15 min
          (aba Actions → "Poller de partidas").
        </Empty>
      ) : (
        <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
          <table className="board">
            <thead>
              <tr>
                <th style={{ textAlign: "left", paddingLeft: 16 }}>Quando</th>
                <th>Status</th><th>Novas</th><th>Promovidas</th><th>Chamadas API</th><th>429</th>
              </tr>
            </thead>
            <tbody>
              {st.runs.map((r) => (
                <tr key={r.id} title={(r.errors || []).join("\n")}>
                  <td style={{ textAlign: "left", paddingLeft: 16 }} className="mono">{dateTime(r.started_raw)}</td>
                  <td className={!r.finished_raw ? "" : r.ok ? "win" : "loss"}>
                    {!r.finished_raw ? "rodando" : r.ok ? (r.errors?.length ? "ok c/ avisos" : "ok") : "falhou"}
                  </td>
                  <td>{r.new_matches ?? "—"}</td>
                  <td>{r.deep_upgraded ?? "—"}</td>
                  <td>{r.api_calls ?? "—"}</td>
                  <td>{r.rate_limited ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
