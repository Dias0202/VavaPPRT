import { useAuth } from "../lib/auth";
import PlayerProfile from "../components/PlayerProfile";

export default function Dashboard() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <>
      <div className="page-head">
        <span className="eyebrow">Seu desempenho</span>
        <h1>{user.riot_id}<span className="muted">#{user.tagline}</span></h1>
      </div>
      <PlayerProfile riotId={user.riot_id} tagline={user.tagline} />
    </>
  );
}
