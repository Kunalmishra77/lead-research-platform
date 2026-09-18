# Phase 10 — Scaling & Reliability

## Goal
Handle ~1M pages/day and millions of companies reliably with measured costs.

## Read before starting
docs/01 (scaling triggers), 13 (monitoring), 04 (partitioning), 02 (production column)

## Tasks
- [ ] 10.1 Load test harness (k6 for API, synthetic crawl targets for workers); baseline report
- [ ] 10.2 Partitioning live for field_values, raw_documents, usage_events, audit_logs + retention jobs
- [ ] 10.3 PgBouncer, read replica for search/export queries
- [ ] 10.4 Search index service (Typesense or OpenSearch) fed by outbox CDC; hybrid keyword + vector + filters; ADR
- [ ] 10.5 Evaluate Temporal for research workflows (ADR); migrate if criteria met
- [ ] 10.6 Container orchestration (Kubernetes/ECS) with autoscaling on stream lag per pool; Terraform
- [ ] 10.7 Grafana dashboards + alerts for all metrics in docs/13; on-call runbooks in docs/runbooks
- [ ] 10.8 Cost dashboards: cost per lead by source/depth/tenant; AI cost by task/model
- [ ] 10.9 Self-hosted extraction model trial (vLLM) vs hosted: eval + cost comparison ADR
- [ ] 10.10 Backups/PITR verification and restore drill; disaster recovery doc

## Acceptance criteria
- Sustained 1M pages/day for 24 h in staging with crawl success >= 85% (on permitted targets) and no queue growth.
- Search p95 < 300 ms at 5M companies.
- Restore drill completed within RTO 4 h, RPO 15 min.
- Cost per Standard lead within target and visible on dashboard.
