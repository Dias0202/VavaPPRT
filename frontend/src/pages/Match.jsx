// Uma partida aberta: placar dos 10, timeline de rounds com economia e
// abertura, e o mapa de onde saíram as kills. Adversários aparecem só como
// linha do placar (sem perfil, sem tag) — scouting não consentido não.
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, playerPath } from "../lib/api";
import { AgentAvatar } from "../components/Stats";
import { Empty, Loader } from "../components/blocks";
import { loadMapAssets, paintBlobs } from "../lib/minimap";
import { dateTime } from "../lib/time";

const BUY = { eco: "E", force: "F", full: "C", unknown: "?" };
const BUY_LABEL = { eco: "eco", force: "força", full: "compra cheia", unknown: "?" };
const RESULT = { Elimination: "eliminação", Defuse: "defuse", Detonate: "explosão", "Bomb defused": "defuse", "Bomb detonated": "explosão" };

export default function Match() {
  const { matchId } = useParams();
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setD(null); setErr("");
    api.match(matchId).then(setD).catch((e) => setErr(e.message));
  }, [matchId]);

  if (err) return <div className="error-box">{err}</div>;
  if (!d) return <Loader />;

  const ours = d.teams.find((t) => t.ours);
  const theirs = d.teams.find((t) => !t.ours);
  return (
    <>
      <div className="page-head">
        <span className="eyebrow">
          Partida · {d.mode} · {dateTime(d.started_raw)}
        </span>
        <h1>
          {d.map}{" "}
          {ours && (
            <span className={ours.won ? "win" : "loss"}>
              {ours.rounds_won}<span className="muted">–</span>{theirs?.rounds_won ?? ours.rounds_lost}
            </span>
          )}
        </h1>
      </div>

      {!d.full && (
        <div className="error-box" style={{ borderColor: "var(--muted-2)", background: "rgba(126,140,153,0.08)" }}>
          Esta partida ainda está só em resumo (veio do histórico antigo). O poller
          vai buscar os rounds dela em breve; até lá, só o placar do grupo.
        </div>
      )}

      <Scoreboard players={d.players} ourTeam={d.our_team} />
      {d.full && <Timeline rounds={d.rounds_detail} ourTeam={d.our_team} />}
      {d.full && d.kills.length > 0 && <MatchHeat map={d.map} kills={d.kills} rounds={d.rounds_detail} />}
    </>
  );
}

function Scoreboard({ players, ourTeam }) {
  const teams = [...new Set(players.map((p) => p.team))];
  return (
    <>
      {teams.map((t) => (
        <section key={t}>
          <div className="section-title">
            <span className="eyebrow">{t === ourTeam ? "Nosso time" : ourTeam ? "Adversários" : `Time ${t}`}</span>
          </div>
          <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
            <table className="board scoreboard">
              <thead>
                <tr>
                  <th style={{ textAlign: "left", paddingLeft: 16 }}>Jogador</th>
                  <th>Elo</th><th>K</th><th>D</th><th>A</th><th>ACS</th><th>ADR</th><th>HS%</th><th>FK</th>
                </tr>
              </thead>
              <tbody>
                {players.filter((p) => p.team === t).map((p, i) => (
                  <tr key={`${p.name}-${i}`} className={p.friend ? "friend" : ""}>
                    <td style={{ textAlign: "left", paddingLeft: 12 }}>
                      <span className="who-cell">
                        <AgentAvatar name={p.agent} size={30} />
                        {p.friend && p.riot_id
                          ? <Link to={playerPath({ riot_id: p.riot_id, tagline: p.tag })}><b>{p.name}</b></Link>
                          : <span>{p.name}</span>}
                        {p.party && <span className="party" title="Mesma party">P{p.party}</span>}
                      </span>
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>{p.tier_name || "—"}</td>
                    <td>{p.kills}</td><td>{p.deaths}</td><td>{p.assists}</td>
                    <td><b>{p.acs}</b></td><td>{p.adr}</td><td>{p.hs_pct}%</td>
                    <td>{p.first_kills ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}

function Timeline({ rounds, ourTeam }) {
  const [sel, setSel] = useState(null);
  const r = sel !== null ? rounds.find((x) => x.round === sel) : null;
  const theirTeam = r && Object.keys(r.economy).find((t) => t !== ourTeam);
  return (
    <>
      <div className="section-title"><span className="eyebrow">Rounds — clique para detalhar</span></div>
      <div className="panel">
        <div className="round-strip" role="list">
          {rounds.map((x) => {
            const econ = x.economy?.[ourTeam];
            return (
              <button
                key={x.round}
                role="listitem"
                className={`round-cell ${x.won ? "w" : "l"} ${sel === x.round ? "sel" : ""} ${x.round === 12 ? "half" : ""}`}
                onClick={() => setSel(sel === x.round ? null : x.round)}
                title={`Round ${x.round + 1}: ${x.won ? "vitória" : "derrota"} (${RESULT[x.result] || x.result || ""})`}
              >
                <span className="n">{x.round + 1}</span>
                <span className="b">{econ ? BUY[econ.buy] : ""}</span>
                <span className={`fk ${x.first_kill ? (x.first_kill.ours ? "ours" : "theirs") : ""}`} />
              </button>
            );
          })}
        </div>
        <div className="muted mono" style={{ fontSize: 11, marginTop: 10 }}>
          letra = nossa compra (E eco · F força · C cheia) · traço = quem abriu o round (ciano nós, âmbar eles)
        </div>
        {r && (
          <div className="round-detail">
            <div><span className="eyebrow">Round {r.round + 1}</span>{" "}
              <b className={r.won ? "win" : "loss"}>{r.won ? "Vitória" : "Derrota"}</b>
              <span className="muted"> · {RESULT[r.result] || r.result}{r.plant_site ? ` · plant no ${r.plant_site}` : ""}</span>
            </div>
            {r.first_kill && (
              <div>Abertura: <b>{r.first_kill.killer}</b> matou <b>{r.first_kill.victim}</b>{" "}
                <span className={r.first_kill.ours ? "win" : "loss"}>({r.first_kill.ours ? "nossa" : "deles"})</span>
              </div>
            )}
            <div className="muted">
              Economia: nós {BUY_LABEL[r.economy?.[ourTeam]?.buy] || "?"} ({r.economy?.[ourTeam]?.avg_loadout ?? "—"})
              {theirTeam && <> · eles {BUY_LABEL[r.economy[theirTeam].buy]} ({r.economy[theirTeam].avg_loadout ?? "—"})</>}
            </div>
            <div className="muted">
              Vivos no fim (grupo): {r.friends_alive.length ? r.friends_alive.join(", ") : "ninguém"}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

const SIZE = 560;

function MatchHeat({ map, kills }) {
  const ref = useRef(null);
  const [asset, setAsset] = useState(undefined);
  const [mode, setMode] = useState("group"); // group | team

  useEffect(() => {
    loadMapAssets().then((a) => setAsset(a[(map || "").toLowerCase()] || null));
  }, [map]);

  useEffect(() => {
    const cv = ref.current;
    if (!cv || !asset) return;
    cv.width = SIZE; cv.height = SIZE;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, SIZE, SIZE);
    const good = mode === "group" ? kills.filter((k) => k.friend_killer) : kills.filter((k) => k.ours);
    const bad = mode === "group" ? kills.filter((k) => k.friend_victim) : kills.filter((k) => !k.ours);
    paintBlobs(ctx, bad, asset, SIZE, "240,165,58", 20, 0.55);
    paintBlobs(ctx, good, asset, SIZE, "111,227,212", 20, 0.55);
  }, [asset, kills, mode]);

  if (asset === undefined) return <Loader />;
  if (!asset) return <Empty>Mapa sem minimapa na Valorant-API.</Empty>;
  return (
    <>
      <div className="flex between" style={{ flexWrap: "wrap", marginTop: 8 }}>
        <div className="section-title" style={{ flex: 1 }}><span className="eyebrow">Onde as mortes aconteceram</span></div>
        <div className="tabs" style={{ borderBottom: "none", margin: 0 }}>
          {[["group", "Grupo"], ["team", "Time inteiro"]].map(([k, l]) => (
            <button key={k} className={mode === k ? "active" : ""} onClick={() => setMode(k)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="heat-wrap" style={{ aspectRatio: "1 / 1", maxWidth: SIZE }}>
        <img src={asset.displayIcon} alt={map} style={{ opacity: 0.45 }} />
        <canvas ref={ref} />
      </div>
      <div className="heat-legend">
        <span className="k">{mode === "group" ? "kills do grupo" : "kills nossas"}</span>
        <span className="d">{mode === "group" ? "mortes do grupo" : "mortes nossas"}</span>
      </div>
    </>
  );
}
