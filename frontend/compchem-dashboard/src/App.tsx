import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AuditPage from "./pages/AuditPage";
import CampaignDetailPage from "./pages/CampaignDetailPage";
import CampaignsPage from "./pages/CampaignsPage";
import DemoLandingPage from "./pages/DemoLandingPage";
import MethodsExportPage from "./pages/MethodsExportPage";
import MoleculePage from "./pages/MoleculePage";
import RunPage from "./pages/RunPage";
import SarPage from "./pages/SarPage";
import SarScatterPage from "./pages/SarScatterPage";
import WetlabAuditPage from "../../wetlab-dashboard/src/pages/AuditPage";
import WetlabBatchComparisonPage from "../../wetlab-dashboard/src/pages/BatchComparisonPage";
import WetlabBatchTimelinePage from "../../wetlab-dashboard/src/pages/BatchTimelinePage";
import WetlabCampaignDetailPage from "../../wetlab-dashboard/src/pages/CampaignDetailPage";
import WetlabCampaignsPage from "../../wetlab-dashboard/src/pages/CampaignsPage";
import WetlabMethodsExportPage from "../../wetlab-dashboard/src/pages/MethodsExportPage";

export default function App() {
  return (
    <Routes>
      <Route path="/demo" element={<DemoLandingPage />} />
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

        <Route path="/wetlab" element={<Navigate to="/wetlab/campaigns" replace />} />
        <Route path="/wetlab/campaigns" element={<WetlabCampaignsPage />} />
        <Route path="/wetlab/campaigns/:id" element={<WetlabCampaignDetailPage />} />
        <Route path="/wetlab/campaigns/:campaignId/audit" element={<WetlabAuditPage />} />
        <Route path="/wetlab/campaigns/:campaignId/compare" element={<WetlabBatchComparisonPage />} />
        <Route path="/wetlab/campaigns/:campaignId/methods" element={<WetlabMethodsExportPage />} />
        <Route path="/wetlab/campaigns/:campaignId/batches/:batchId/timeline" element={<WetlabBatchTimelinePage />} />
      </Route>
    </Routes>
  );
}
