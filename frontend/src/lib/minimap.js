// Minimapas da Valorant-API (com os multiplicadores de coordenada), buscados
// uma vez por sessão. Usado pelo heatmap do perfil e pelo da partida.
let _maps = null;
let _inflight = null;

export function loadMapAssets() {
  if (_maps) return Promise.resolve(_maps);
  if (!_inflight) {
    _inflight = fetch("https://valorant-api.com/v1/maps")
      .then((r) => r.json())
      .then((j) => {
        _maps = {};
        for (const m of j.data || []) _maps[m.displayName.toLowerCase()] = m;
        return _maps;
      })
      .catch(() => {
        _maps = {};
        return _maps;
      });
  }
  return _inflight;
}

// Coordenada de jogo -> fração 0..1 do minimapa (fórmula padrão da comunidade).
export function toMinimap(x, y, a) {
  return { fx: y * a.xMultiplier + a.xScalarToAdd, fy: x * a.yMultiplier + a.yScalarToAdd };
}

// Pinta manchas radiais (heatmap) num canvas quadrado de `size` px.
export function paintBlobs(ctx, pts, asset, size, rgb, radius = 26, alpha = 0.45) {
  ctx.globalCompositeOperation = "lighter";
  for (const p of pts) {
    const { fx, fy } = toMinimap(p.x, p.y, asset);
    const px = fx * size;
    const py = fy * size;
    if (px < 0 || px > size || py < 0 || py > size) continue;
    const g = ctx.createRadialGradient(px, py, 0, px, py, radius);
    g.addColorStop(0, `rgba(${rgb},${alpha})`);
    g.addColorStop(1, `rgba(${rgb},0)`);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(px, py, radius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";
}
