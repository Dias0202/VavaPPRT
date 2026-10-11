// Frescor do dado no topo da página: quando o poller rodou pela última vez
// e se a última execução falhou. Um poller parado não pode ser silencioso.
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ago } from "../lib/time";

const STALE_S = 3 * 3600; // poller roda a cada ~15 min; 3 h sem rodar = parado

export default function IngestChip() {
  const [st, setSt] = useState(null);

  useEffect(() => {
    let on = true;
    const load = () => api.ingestStatus().then((d) => on && setSt(d)).catch(() => {});
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => { on = false; clearInterval(t); };
  }, []);

  if (!st) return null;
  const last = st.runs?.[0];
  const failed = last && last.finished_raw && !last.ok;
  const stale = !st.last_ok_raw || st.now_raw - st.last_ok_raw > STALE_S;
  const downloading = st.players?.filter((p) => p.downloading).length || 0;
  const tone = failed || stale ? "warn" : "ok";
  const title = failed
    ? `Última execução do poller falhou: ${(last.errors || []).join(" · ") || "erro"}`
    : `Última atualização ${ago(st.last_ok_raw, st.now_raw)}`;

  return (
    <span className={`ingest-chip ${tone}`} title={title}>
      <span className="dot" aria-hidden="true" />
      {downloading
        ? `baixando ${downloading} ${downloading === 1 ? "jogador" : "jogadores"}…`
        : st.last_ok_raw
          ? `dados ${ago(st.last_ok_raw, st.now_raw)}`
          : "aguardando 1ª coleta"}
      {failed && " · falhou"}
    </span>
  );
}
