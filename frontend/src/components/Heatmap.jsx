import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { Loader, Empty } from "./blocks";
import { loadMapAssets, paintBlobs } from "../lib/minimap";

const SIZE = 600;

export default function Heatmap({ riotId, tagline }) {
  const [maps, setMaps] = useState(null);
  const [sel, setSel] = useState(null);
  const [data, setData] = useState(null);
  const [asset, setAsset] = useState(null);
  const [mode, setMode] = useState("deaths"); // deaths | kills | both
  const canvasRef = useRef(null);

  useEffect(() => {
    api.maps(riotId, tagline).then((rows) => {
      const withData = rows.filter((m) => m.matches > 0);
      setMaps(withData);
      if (withData.length) setSel(withData[0].map);
    });
  }, [riotId, tagline]);

  useEffect(() => {
    if (!sel) return;
    setData(null);
    Promise.all([api.heatmap(riotId, tagline, sel), loadMapAssets()]).then(([hm, assets]) => {
      setData(hm);
      setAsset(assets[sel.toLowerCase()] || null);
    });
  }, [sel, riotId, tagline]);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !data || !asset) return;
    cv.width = SIZE; cv.height = SIZE;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, SIZE, SIZE);

    if (mode !== "kills") paintBlobs(ctx, data.deaths || [], asset, SIZE, "240,165,58");
    if (mode !== "deaths") paintBlobs(ctx, data.kills || [], asset, SIZE, "111,227,212");
  }, [data, asset, mode]);

  if (!maps) return <Loader />;
  if (!maps.length) return <Empty>Sem dados de posição ainda (precisa de partidas completas).</Empty>;

  return (
    <div>
      <div className="flex between" style={{ flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
        <div className="tabs" style={{ borderBottom: "none", margin: 0 }}>
          {maps.map((m) => (
            <button key={m.map} className={sel === m.map ? "active" : ""} onClick={() => setSel(m.map)}>{m.map}</button>
          ))}
        </div>
        <div className="tabs" style={{ borderBottom: "none", margin: 0 }}>
          {[["deaths", "Mortes"], ["kills", "Kills"], ["both", "Ambos"]].map(([k, l]) => (
            <button key={k} className={mode === k ? "active" : ""} onClick={() => setMode(k)}>{l}</button>
          ))}
        </div>
      </div>
      {!data ? <Loader /> : (
        <>
          <div className="heat-wrap" style={{ aspectRatio: "1 / 1" }}>
            {asset?.displayIcon && <img src={asset.displayIcon} alt={sel} style={{ opacity: 0.45 }} />}
            <canvas ref={canvasRef} />
          </div>
          <div className="heat-legend">
            {mode !== "kills" && <span className="d">Mortes ({data.deaths?.length || 0})</span>}
            {mode !== "deaths" && <span className="k">Kills ({data.kills?.length || 0})</span>}
          </div>
          {!asset && <p className="muted center" style={{ marginTop: 12, fontSize: 13 }}>
            Mapa sem minimapa na Valorant-API — pontos não renderizados.</p>}
          <p className="muted center" style={{ marginTop: 8, fontSize: 12 }}>
            Posições só de partidas completas (ingestão v4). Clusters de vermelho = onde você morre demais.
          </p>
        </>
      )}
    </div>
  );
}
