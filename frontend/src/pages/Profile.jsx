import { useParams, Link } from "react-router-dom";
import PlayerProfile from "../components/PlayerProfile";

export default function Profile() {
  const { riotId, tagline } = useParams();
  return (
    <>
      <div className="page-head">
        <Link to="/ranking" className="eyebrow">← Ranking</Link>
        <h1>{riotId}<span className="muted">#{tagline}</span></h1>
      </div>
      <PlayerProfile key={`${riotId}/${tagline}`} riotId={riotId} tagline={tagline} />
    </>
  );
}
