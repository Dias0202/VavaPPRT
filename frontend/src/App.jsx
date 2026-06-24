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

const pageVariants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.25, ease: [0.4, 0, 0.2, 1] } },
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
  return user ? children : <Navigate to="/login" replace />;
}

function PublicOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <SkeletonLoader />;
  return user ? <Navigate to="/" replace /> : children;
}

export default function App() {
  const location = useLocation();
  return (
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
          <Route path="/jogador/:riotId/:tagline" element={<PageMotion><Profile /></PageMotion>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AnimatePresence>
  );
}
