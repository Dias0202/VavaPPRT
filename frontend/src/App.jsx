import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "./lib/auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Leaderboard from "./pages/Leaderboard";
import Profile from "./pages/Profile";
import Group from "./pages/Group";
import Weekly from "./pages/Weekly";
import Goals from "./pages/Goals";
import Account from "./pages/Account";
import Pending from "./pages/Pending";
import Admin from "./pages/Admin";
import Duo from "./pages/Duo";
import Match from "./pages/Match";
import PreQueue from "./pages/PreQueue";
import ServerBanner from "./components/ServerBanner";

// A transição de página desliza, mas NÃO parte de opacity 0.
//
// Com opacity inicial 0 o conteúdo inteiro fica atrás de uma animação movida a
// requestAnimationFrame: se o rAF for estrangulado (aba em segundo plano,
// aparelho lento, CPU ocupada), a página fica em branco até ele voltar. O
// deslize dá a mesma sensação e o texto continua legível se a animação
// simplesmente não rodar.
const pageVariants = {
  initial: { y: 10 },
  animate: { y: 0, transition: { duration: 0.25, ease: [0.4, 0, 0.2, 1] } },
  exit: { opacity: 0, y: -8, transition: { duration: 0.15 } },
};

export function PageMotion({ children }) {
  return (
    <motion.div
      className="page-motion"
      variants={pageVariants}
      initial="initial"
      animate="animate"
      exit="exit"
    >
      {children}
    </motion.div>
  );
}

function SkeletonLoader() {
  return (
    <div style={{ padding: "40px", maxWidth: 800, margin: "0 auto" }}>
      <div className="skeleton skeleton-text" style={{ width: "30%", marginBottom: 20 }} />
      <div className="skeleton skeleton-stat" style={{ marginBottom: 12 }} />
      <div className="skeleton skeleton-stat" />
    </div>
  );
}

function Protected({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <SkeletonLoader />;
  if (!user) return <Navigate to="/login" replace />;
  // Cadastro pendente não vê o grupo — só a tela de espera.
  if (user.status && user.status !== "approved") return <Pending />;
  return children;
}

function AdminOnly({ children }) {
  const { user } = useAuth();
  return user?.is_admin ? children : <Navigate to="/" replace />;
}

function PublicOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <SkeletonLoader />;
  return user ? <Navigate to="/" replace /> : children;
}

export default function App() {
  const location = useLocation();
  return (
    <>
    <ServerBanner />
    <AnimatePresence mode="wait">
      <Routes location={location} key={location.pathname}>
        <Route path="/login" element={<PublicOnly><PageMotion><Login /></PageMotion></PublicOnly>} />
        <Route path="/registrar" element={<PublicOnly><PageMotion><Register /></PageMotion></PublicOnly>} />
        <Route
          element={
            <Protected>
              <Layout />
            </Protected>
          }
        >
          <Route path="/" element={<PageMotion><Dashboard /></PageMotion>} />
          <Route path="/ranking" element={<PageMotion><Leaderboard /></PageMotion>} />
          <Route path="/grupo" element={<PageMotion><Group /></PageMotion>} />
          <Route path="/semana" element={<PageMotion><Weekly /></PageMotion>} />
          <Route path="/metas" element={<PageMotion><Goals /></PageMotion>} />
          <Route path="/conta" element={<PageMotion><Account /></PageMotion>} />
          <Route path="/jogador/:riotId/:tagline" element={<PageMotion><Profile /></PageMotion>} />
          <Route path="/pre-fila" element={<PageMotion><PreQueue /></PageMotion>} />
          <Route path="/partida/:matchId" element={<PageMotion><Match /></PageMotion>} />
          <Route path="/dupla/:ra/:ta/:rb/:tb" element={<PageMotion><Duo /></PageMotion>} />
          <Route path="/admin" element={<AdminOnly><PageMotion><Admin /></PageMotion></AdminOnly>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AnimatePresence>
    </>
  );
}
