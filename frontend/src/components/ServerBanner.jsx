// Faixa "acordando o servidor". O Render free hiberna: a primeira requisição
// depois de um tempo parado leva até ~1 min. Sem aviso, isso parece site
// quebrado; com aviso, é só esperar.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { onServerState } from "../lib/api";

export default function ServerBanner() {
  const [state, setState] = useState("ok");
  const [secs, setSecs] = useState(0);

  useEffect(() => onServerState(setState), []);
  useEffect(() => {
    if (state !== "waking") return undefined;
    setSecs(0);
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  return (
    <AnimatePresence>
      {state === "waking" && (
        <motion.div
          className="server-banner"
          role="status"
          initial={{ y: -40 }}
          animate={{ y: 0 }}
          exit={{ y: -40 }}
          transition={{ duration: 0.2 }}
        >
          <span className="pulse" aria-hidden="true" />
          Acordando o servidor — ele hiberna quando ninguém usa. Leva até um
          minuto na primeira abertura
          <span className="mono muted"> · {secs + 4}s</span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
