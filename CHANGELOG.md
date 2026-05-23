# LabLink AI — Changelog

| Date | Change |
|---|---|
| 2026-05-22 | Verticalized the bioprocess dashboard: renamed `frontend/compchem-dashboard` → `frontend/wetlab-dashboard`, deleted all comp-chem pages (Audit/Molecule/Run/Sar/SarScatter/MethodsExport/DemoLogin) and types, rebranded Layout (LabLink Bioprocess), trimmed api/client.ts to wet lab only, new `CampaignsPage` lists wet lab campaigns via `GET /api/v1/campaigns?domain=wetlab`, new `CampaignDetailPage`; comp-chem dashboard remains on `pivot/compchem-campaigns` |
| 2026-05-22 | Wet lab dashboard: `BatchTimelinePage` (Recharts ComposedChart with continuous lines + offline diamond scatter, parameter toggles, derived QC flags), `BatchComparisonPage` (final titer / peak VCD BarChart); new endpoints `GET /campaigns`, `/campaigns/{id}`, `/campaigns/{id}/batches`, `/batches/{id}`, `/batches/{id}/timeseries`, `/batches/{id}/samples` |
| 2026-05-22 | `seed_demo_wetlab.py`: mAb Process Dev Campaign 4 with three batches (004A/B/C), 14-day continuous traces (pH/DO/temp/agitation) at 2h cadence, daily offline samples (VCD/viability/titer/glucose/lactate/osmolality), cro_delivery + lead_nominated audit events |
| 2026-05-22 | Wet lab entities: `campaigns` (with `domain` discriminator), `batches`, `timeseries_data`, `offline_samples`; migration `003_wetlab` |
| 2026-05-22 | Bioprocess pivot: runs, measurement_series, API keys, bioprocess parsers, domain QC, dashboard, auth, SOC 2 compliance endpoint; bug fixes in runs_service, timeseries_align, migration |
| 2026-05-21 | Initial CLAUDE.md created from full codebase scan |
