import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <>
      <header className="topbar">
        <div className="container inner">
          <NavLink to="/" className="brand">
            VAVA<span className="accent">PPRT</span>
          </NavLink>
          <nav className="nav">
            <NavLink to="/" end>Painel</NavLink>
            <NavLink to="/ranking">Ranking</NavLink>
            <NavLink to="/grupo">Grupo</NavLink>
          </nav>
          <div className="spacer" />
          <span className="muted mono" style={{ fontSize: 12 }}>
            {user?.riot_id}#{user?.tagline}
          </span>
          <button className="btn ghost" style={{ padding: "7px 14px" }} onClick={logout}>
            Sair
          </button>
        </div>
      </header>
      <main className="page">
        <div className="container">
          <Outlet />
        </div>
      </main>
    </>
  );
}
