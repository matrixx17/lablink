import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AuditPage from "./pages/AuditPage";
import CampaignDetailPage from "./pages/CampaignDetailPage";
import CampaignsPage from "./pages/CampaignsPage";
import MoleculePage from "./pages/MoleculePage";
import RunPage from "./pages/RunPage";
import SarPage from "./pages/SarPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/campaigns" replace />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
        <Route path="/campaigns/:id/molecules" element={<SarPage />} />
        <Route path="/molecules/:id" element={<MoleculePage />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/audit/:campaignId" element={<AuditPage />} />
      </Route>
    </Routes>
  );
}
