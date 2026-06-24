import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { PlayerLink } from "../components/Stats";

export default function Leaderboard() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.leaderboard().then(setRows).catch((e) => setErr(e.message));
  }, []);

  return (
    <>
      <div className="page-head">
        <span className="eyebrow">O grupo</span>
        <h1>Ranking do Squad</h1>
      </div>
      <div className="tac-bar" style={{ marginBottom: 18 }} />
      {err && <div className="error-box">{err}</div>}
      {!rows ? (
        <div className="loader">Carregando ranking…</div>
      ) : rows.length === 0 ? (
        <div className="empty">Ninguém cadastrado ainda.</div>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          <table className="board">
            <thead>
              <tr>
                <th>#</th>
                <th>Jogador</th>
                <th>Partidas</th>
                <th>Vitória</th>
                <th>K/D</th>
                <th>KAST</th>
                <th>ACS</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.riot_id}#${r.tagline}`}>
                  <td className="rank">{r.rank}</td>
                  <td className="who">
                    <PlayerLink riot_id={r.riot_id} tagline={r.tagline}>
                      <div className="u">{r.username}</div>
                      <div className="r">{r.riot_id}#{r.tagline}</div>
                    </PlayerLink>
                  </td>
                  <td>{r.matches}</td>
                  <td className={r.win_rate >= 50 ? "win" : "loss"}>{r.win_rate}%</td>
                  <td>{r.kd}</td>
                  <td>{r.kast != null ? `${r.kast}%` : "—"}</td>
                  <td>{r.acs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
