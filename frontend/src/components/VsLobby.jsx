// Você contra o lobby: os outros 9 de cada partida e a média do seu elo.
// É a pergunta "eu sou o problema ou o time é?", respondida com número.
import { Conf, Empty } from "./blocks";

const METRICS = [
  ["acs", "ACS"], ["kd", "K/D"], ["adr", "ADR"], ["hs_pct", "HS%"],
];

function Delta({ me, ref_ }) {
  if (me == null || ref_ == null) return <span className="muted">—</span>;
  const d = me - ref_;
  const pct = ref_ ? Math.round((d / ref_) * 100) : 0;
  const cls = pct >= 3 ? "win" : pct <= -3 ? "loss" : "muted";
  return <span className={cls}>{pct > 0 ? "+" : ""}{pct}%</span>;
}

export default function VsLobby({ data }) {
  const { lobby, rank } = data;
  if (!lobby.matches) return <Empty>Precisa de partidas completas para comparar com o lobby.</Empty>;
  return (
    <>
      <div className="stat-grid">
        <div className="stat">
          <div className="k">Posição média no placar</div>
          <div className="v">{lobby.avg_place}<small>º/10</small></div>
          <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>
            {lobby.avg_team_place}º no próprio time
          </div>
        </div>
        <div className="stat">
          <div className="k">Top 1 do time</div>
          <div className="v">{lobby.team_mvp_pct}<small>%</small></div>
          <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>esperado: 20%</div>
        </div>
        <div className="stat">
          <div className="k">Top 1 da partida</div>
          <div className="v">{lobby.match_mvp_pct}<small>%</small></div>
          <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>esperado: 10%</div>
        </div>
        <div className="stat">
          <div className="k">Amostra</div>
          <div className="v">{lobby.matches}</div>
          <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>
            <Conf level={lobby.confidence} n={lobby.matches} />
          </div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", alignItems: "start" }}>
        <section>
          <div className="section-title"><span className="eyebrow">Você vs o lobby</span></div>
          <div className="panel" style={{ padding: 0 }}>
            <table className="board">
              <thead>
                <tr>
                  <th style={{ textAlign: "left", paddingLeft: 16 }}>Métrica</th>
                  <th>Você</th><th>Seu time</th><th>Adversários</th><th>vs adv.</th>
                </tr>
              </thead>
              <tbody>
                {METRICS.map(([k, label]) => (
                  <tr key={k}>
                    <td style={{ textAlign: "left", paddingLeft: 16 }}>{label}</td>
                    <td><b>{lobby.me?.[k]}</b></td>
                    <td>{lobby.teammates?.[k]}</td>
                    <td>{lobby.enemies?.[k]}</td>
                    <td><Delta me={lobby.me?.[k]} ref_={lobby.enemies?.[k]} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <div className="section-title">
            <span className="eyebrow">Você vs o seu elo</span>
            {rank && <Conf level={rank.confidence} n={rank.sample} />}
          </div>
          {!rank || !rank.baseline ? (
            <Empty>Sem rank conhecido ainda (jogue ranqueada ou aguarde a próxima coleta).</Empty>
          ) : (
            <div className="panel" style={{ padding: 0 }}>
              <table className="board">
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", paddingLeft: 16 }}>Métrica</th>
                    <th>Você</th><th>Média {rank.tier_name}</th><th>Diferença</th>
                  </tr>
                </thead>
                <tbody>
                  {METRICS.map(([k, label]) => (
                    <tr key={k}>
                      <td style={{ textAlign: "left", paddingLeft: 16 }}>{label}</td>
                      <td><b>{rank.me?.[k]}</b></td>
                      <td>{rank.baseline[k]}</td>
                      <td><Delta me={rank.me?.[k]} ref_={rank.baseline[k]} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12, padding: "10px 16px", margin: 0 }}>
                Média de {rank.sample} jogadores-partida em {rank.band.join(", ")} vistos nas
                partidas do grupo (você fora da conta).
              </p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
