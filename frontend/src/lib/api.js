// Cliente HTTP fino para a API VavaPPRT. Anexa o token JWT e normaliza erros.

const BASE = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "vavapprt_token";

// O site pode estar publicado antes de existir um backend. Nesse caso o
// endereço da API ainda aponta para localhost, e a falha precisa dizer isso em
// vez de "não foi possível falar com o servidor".
const IS_LOCAL_API = /^https?:\/\/(localhost|127\.0\.0\.1)/.test(BASE);
const PAGE_IS_REMOTE =
  typeof window !== "undefined" &&
  !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);

export const apiBase = BASE;
export const apiMisconfigured = IS_LOCAL_API && PAGE_IS_REMOTE;

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

// Estado do servidor para a UI. O Render free hiberna depois de 15 min sem
// uso e leva ~30–60 s para acordar: a requisição não falha, ela DEMORA. Em vez
// de um erro de timeout, a interface mostra "acordando o servidor".
let serverState = "ok"; // "ok" | "waking"
let slowInflight = 0;
const listeners = new Set();
function setServerState(s) {
  if (s === serverState) return;
  serverState = s;
  listeners.forEach((fn) => fn(s));
}
export function onServerState(fn) {
  listeners.add(fn);
  fn(serverState);
  return () => listeners.delete(fn);
}

const SLOW_MS = 4000;      // depois disso, provavelmente é o servidor acordando
const TIMEOUT_MS = 75000;  // cold start do Render cabe aqui com folga
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function request(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  let isSlow = false;
  const slow = setTimeout(() => {
    isSlow = true;
    slowInflight += 1;
    setServerState("waking");
  }, SLOW_MS);
  try {
    for (let attempt = 0; ; attempt++) {
      try {
        // Timeout explícito: um backend inalcançável costuma PENDURAR o
        // preflight em vez de recusar. Sem isto o botão fica travado.
        res = await fetch(`${BASE}${path}`, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(TIMEOUT_MS),
        });
      } catch (e) {
        if (apiMisconfigured) {
          throw new Error(
            "Este site ainda não está ligado a um servidor. Quem administra o grupo " +
              "precisa definir a variável VITE_API_URL com o endereço da API e republicar."
          );
        }
        if (attempt < 1 && method === "GET") {
          await sleep(3000);
          continue;
        }
        throw new Error(
          `Servidor não respondeu (${BASE}). Ele pode estar acordando — ` +
            "tente de novo em alguns segundos."
        );
      }
      // Durante deploy/boot o proxy do Render devolve 502/503 por alguns segundos.
      if ((res.status === 502 || res.status === 503) && method === "GET" && attempt < 3) {
        await sleep(4000);
        continue;
      }
      break;
    }
  } finally {
    clearTimeout(slow);
    if (isSlow) slowInflight -= 1;
    if (slowInflight === 0) setServerState("ok");
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
  changePassword: (current, next) =>
    request("/me/password", { method: "PUT", body: { current, new: next } }),
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
  groupCorrelation: (members) =>
    members ? request("/group/correlation", { method: "POST", body: { members } })
            : request("/group/correlation"),
  synergyMatrix: () => request("/group/synergy-matrix"),
  preQueue: (members) => request("/group/pre-queue", { method: "POST", body: { members } }),
  match: (id) => request(`/matches/${encodeURIComponent(id)}`),
  vsLobby: (r, t) => request(p(r, t, "/vs-lobby")),
  ingestStatus: () => request("/status/ingest"),
  validateMetrics: (limit = 5) => request(`/validate/metrics?limit=${limit}`),

  // Progresso no tempo
  trend: (r, t, w = 20) => request(p(r, t, `/trend?window=${w}`)),
  sessions: (r, t) => request(p(r, t, "/sessions")),
  rank: (r, t) => request(p(r, t, "/rank")),
  postplant: (r, t, map) => request(p(r, t, `/postplant${map ? `?map=${encodeURIComponent(map)}` : ""}`)),
  utility: (r, t) => request(p(r, t, "/utility")),
  coverage: (r, t) => request(p(r, t, "/coverage")),

  // Metas de treino
  goals: () => request("/me/goals"),
  addGoal: (payload) => request("/me/goals", { method: "POST", body: payload }),
  deleteGoal: (id) => request(`/me/goals/${id}`, { method: "DELETE" }),

  // Relatório semanal do grupo
  weekly: () => request("/group/weekly"),

  // Rotação de mapas + plano de fila
  mapPool: () => request("/config/map-pool"),
  setMapPool: (maps) => request("/admin/map-pool", { method: "PUT", body: { maps } }),
  queuePlan: () => request("/group/queue-plan"),
  headToHead: (ra, ta, rb, tb) =>
    request(`/group/head-to-head/${encodeURIComponent(ra)}/${encodeURIComponent(ta)}/${encodeURIComponent(rb)}/${encodeURIComponent(tb)}`),

  // Opt-out
  deleteMe: (password) =>
    request("/me", { method: "DELETE", body: { password, confirm: "APAGAR" } }),
  removeTracked: (r, t) =>
    request(`/roster/${encodeURIComponent(r)}/${encodeURIComponent(t)}`, { method: "DELETE" }),

  // Administração
  adminUsers: (status) => request(`/admin/users${status ? `?status=${status}` : ""}`),
  approveUser: (id) => request(`/admin/users/${id}/approve`, { method: "POST" }),
  rejectUser: (id) => request(`/admin/users/${id}/reject`, { method: "POST" }),
  setAdmin: (id, value) => request(`/admin/users/${id}/admin?value=${value}`, { method: "POST" }),
};

export const pairPath = (a, b) =>
  `/dupla/${encodeURIComponent(a.riot_id)}/${encodeURIComponent(a.tagline)}/` +
  `${encodeURIComponent(b.riot_id)}/${encodeURIComponent(b.tagline)}`;

export const playerPath = (m) =>
  `/jogador/${encodeURIComponent(m.riot_id)}/${encodeURIComponent(m.tagline)}`;
