// The wet lab dashboard runs inside the unified comp-chem shell. This module
// re-exports useOrgId and withOrg from the unified Layout so wet-lab pages
// can keep their import paths unchanged. The default Layout export is
// preserved for the standalone wet-lab Vite dev server (npm run dev in
// frontend/wetlab-dashboard) but is no longer mounted in the deployed app.
export { default, useOrgId, withOrg } from "../../../compchem-dashboard/src/components/Layout";
