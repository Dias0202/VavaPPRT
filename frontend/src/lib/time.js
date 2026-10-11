// "há 12 min" a partir de um epoch em segundos.
export function ago(raw, nowRaw) {
  if (!raw) return "nunca";
  const now = nowRaw || Math.floor(Date.now() / 1000);
  const s = Math.max(0, now - raw);
  if (s < 90) return "agora há pouco";
  const m = Math.round(s / 60);
  if (m < 60) return `há ${m} min`;
  const h = Math.round(m / 60);
  if (h < 36) return `há ${h} h`;
  return `há ${Math.round(h / 24)} d`;
}

export function dateTime(raw) {
  if (!raw) return "";
  return new Date(raw * 1000).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
