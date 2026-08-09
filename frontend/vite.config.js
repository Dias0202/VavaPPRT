import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: no GitHub Pages de projeto o site fica em /<repo>/, não na raiz.
// O workflow passa VITE_BASE=/VavaPPRT/; em dev e na Vercel fica "/".
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: { port: 5173 },
});
