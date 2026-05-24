import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AuditPage from "./pages/AuditPage";
import CampaignDetailPage from "./pages/CampaignDetailPage";
import CampaignsPage from "./pages/CampaignsPage";
import DemoLoginPage from "./pages/DemoLoginPage";
import MethodsExportPage from "./pages/MethodsExportPage";
import MoleculePage from "./pages/MoleculePage";
import RunPage from "./pages/RunPage";
import SarPage from "./pages/SarPage";
import SarScatterPage from "./pages/SarScatterPage";

export default function App() {
  return (
    <Routes>
      <Route path="/demo" element={<DemoLoginPage />} />
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/campaigns" replace />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
        <Route path="/campaigns/:campaignId/audit" element={<AuditPage />} />
        <Route path="/campaigns/:id/methods-export" element={<MethodsExportPage />} />
        <Route path="/campaigns/:id/molecules" element={<SarPage />} />
        <Route path="/campaigns/:campaignId/sar" element={<SarScatterPage />} />
        <Route path="/molecules/:id" element={<MoleculePage />} />
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/audit/:campaignId" element={<AuditPage />} />
      </Route>
    </Routes>
  );
}
