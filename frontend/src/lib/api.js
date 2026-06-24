// Cliente HTTP fino para a API VavaPPRT. Anexa o token JWT e normaliza erros.

const BASE = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "vavapprt_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Não foi possível falar com o servidor.");
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* sem corpo */
  }

  if (!res.ok) {
    const msg = data?.detail || `Erro ${res.status}`;
    const err = new Error(typeof msg === "string" ? msg : "Falha na requisição.");
    err.status = res.status;
    throw err;
  }
  return data;
}

const p = (riotId, tagline, path) =>
  `/players/${encodeURIComponent(riotId)}/${encodeURIComponent(tagline)}${path}`;

export const api = {
  register: (payload) => request("/auth/register", { method: "POST", body: payload }),
  login: (payload) => request("/auth/login", { method: "POST", body: payload }),
  me: () => request("/me"),
  players: () => request("/players"),
  leaderboard: () => request("/leaderboard"),
  synergy: () => request("/group/synergy"),
  squads: () => request("/group/squads"),
  roster: () => request("/roster"),
  addPlayer: (payload) => request("/roster", { method: "POST", body: payload }),
  groupDetail: (members) => request("/group/detail", { method: "POST", body: { members } }),
  duo: (ra, ta, rb, tb) =>
    request(`/group/duo/${encodeURIComponent(ra)}/${encodeURIComponent(ta)}/${encodeURIComponent(rb)}/${encodeURIComponent(tb)}`),
  overview: (r, t) => request(p(r, t, "/overview")),
  maps: (r, t) => request(p(r, t, "/maps")),
  agents: (r, t) => request(p(r, t, "/agents")),
  weapons: (r, t) => request(p(r, t, "/weapons")),
  insights: (r, t) => request(p(r, t, "/insights")),
  recent: (r, t) => request(p(r, t, "/recent")),
  heatmap: (r, t, map) => request(p(r, t, `/heatmap${map ? `?map=${encodeURIComponent(map)}` : ""}`)),
  spatial: (r, t, map) => request(p(r, t, `/spatial${map ? `?map=${encodeURIComponent(map)}` : ""}`)),
  groupCorrelation: () => request("/group/correlation"),
  roundAnalysis: () => request("/group/round-analysis"),
  validateMetrics: (limit = 5) => request(`/validate/metrics?limit=${limit}`),
};
