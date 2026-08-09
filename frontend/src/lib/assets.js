// Ícones oficiais (Valorant-API.com). Sem rate limit relevante e com CORS
// aberto, então o navegador busca direto — não passa pelo nosso backend.
// Cada catálogo é buscado UMA vez por sessão e compartilhado por todos os
// componentes; sem isso cada linha de arma dispararia sua própria requisição.
import { useEffect, useState } from "react";

const CATALOGS = {
  agents: {
    url: "https://valorant-api.com/v1/agents?isPlayableCharacter=true",
    key: (a) => a.displayName,
    icon: (a) => a.displayIcon,
  },
  weapons: {
    url: "https://valorant-api.com/v1/weapons",
    key: (w) => w.displayName,
    icon: (w) => w.displayIcon,
  },
  maps: {
    url: "https://valorant-api.com/v1/maps",
    key: (m) => m.displayName,
    // listViewIcon, não splash: o splash é 1920px e seriam ~2MB por cartão.
    icon: (m) => m.listViewIcon || m.splash,
  },
};

const cache = {};   // nome do catálogo -> { chave minúscula: url }
const inflight = {};

async function loadCatalog(name) {
  if (cache[name]) return cache[name];
  if (!inflight[name]) {
    const spec = CATALOGS[name];
    inflight[name] = fetch(spec.url)
      .then((r) => r.json())
      .then((j) => {
        const map = {};
        for (const item of j.data || []) {
          const k = spec.key(item);
          if (k) map[k.toLowerCase()] = spec.icon(item);
        }
        cache[name] = map;
        return map;
      })
      .catch(() => {
        cache[name] = {};
        return cache[name];
      });
  }
  return inflight[name];
}

function useIcon(catalog, name) {
  const [src, setSrc] = useState(() => cache[catalog]?.[(name || "").toLowerCase()] || null);
  useEffect(() => {
    let alive = true;
    loadCatalog(catalog).then((map) => {
      if (alive) setSrc(map[(name || "").toLowerCase()] || null);
    });
    return () => { alive = false; };
  }, [catalog, name]);
  return src;
}

export const useAgentIcon = (name) => useIcon("agents", name);
export const useWeaponIcon = (name) => useIcon("weapons", name);
export const useMapImage = (name) => useIcon("maps", name);
