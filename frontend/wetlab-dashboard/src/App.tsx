import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import BatchComparisonPage from "./pages/BatchComparisonPage";
import BatchTimelinePage from "./pages/BatchTimelinePage";
import CampaignDetailPage from "./pages/CampaignDetailPage";
import CampaignsPage from "./pages/CampaignsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/campaigns" replace />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
        <Route
          path="/campaigns/:campaignId/compare"
          element={<BatchComparisonPage />}
        />
        <Route
          path="/campaigns/:campaignId/batches/:batchId/timeline"
          element={<BatchTimelinePage />}
        />
      </Route>
    </Routes>
  );
}
