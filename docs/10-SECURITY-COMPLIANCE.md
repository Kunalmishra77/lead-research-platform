# 10 — Security and Compliance

Not legal advice. Items marked LEGAL must be reviewed by qualified counsel before commercial launch.

## Security controls (implement as phases reach them)
| Control | Requirement | Phase |
| --- | --- | --- |
| Transport | HTTPS only, HSTS, secure cookies (httpOnly, SameSite=Lax), CSRF protection for cookie-auth routes | 1 |
| Passwords/sessions | Supabase Auth (hashing, email confirmation, session revocation, TOTP MFA later); auth calls server-side only; custom SMTP before beta | 1 / 7 |
| RBAC | Permission matrix in `05-BACKEND-API.md`, guard on every route | 1 |
| Tenant isolation | RLS forced on tenant tables; tenant context per transaction; cross-tenant CI tests | 1 |
| Secrets | `.env` local only; prod via secrets manager; gitleaks in CI | 1 |
| Envelope encryption | Per-org data encryption key (DEK) wrapped by master key (KMS/Vault in prod); AES-256-GCM; used for OAuth tokens, provider keys, webhook secrets | 7 |
| API keys | Random 32 bytes, shown once, sha256 hash stored, prefix lookup, scopes, expiry, revoke | 8 |
| Input validation | Zod/Pydantic at every boundary; size limits on uploads and bodies | 1 |
| SSRF | Crawler + webhook sender block private ranges, metadata IPs, non-http schemes | 3 / 8 |
| Export safety | CSV/Excel formula injection prevention (prefix `'` for cells starting = + - @) | 7 |
| Audit logs | Login, role change, export (rows, fields), API key actions, integration connect, deletion, admin impersonation | 1+ |
| Rate limiting/abuse | Per user/org/key limits; export velocity alerts; disposable email signup block | 2 / 7 |
| Dependencies | Dependabot/Renovate, npm audit/pip-audit in CI | 1 |
| Backups | PITR on managed Postgres, daily snapshot, restore drill quarterly | 10 |
| Logging hygiene | Never log tokens, keys, full emails in debug payloads (mask) | 1 |
| Pen test | External test before public launch | 11 |

## Privacy mechanisms in product
- Field classification: `business` (company-level) vs `personal` (named person's contact).
- Global suppression list (sha256 of normalized email/phone/domain); checked before store, display and export.
- Public privacy request portal `/privacy/request`: access, erasure, objection; email verification; admin queue; SLA tracking; erasure propagates to DB, search index, vectors, caches, pending exports; suppression added to prevent re-collection.
- Provenance kept for every value to answer "where did you get my data".
- Retention jobs (see `04-DATABASE.md`).
- Region-aware defaults: EU/UK workspaces hide personal mobile numbers and require lawful-basis acknowledgement before export.
- Acceptable Use: no spam, no harassment, no sensitive profiling; enforce via ToS and abuse detection.

## Compliance checklist (LEGAL)
- [ ] India DPDP Act 2023 and DPDP Rules: applicability, scope of the publicly-available-data exemption, notice/consent needs, Data Principal rights, breach reporting, Significant Data Fiduciary risk, current enforcement timelines
- [ ] GDPR / UK GDPR: controller vs processor role, legitimate interests assessment, Article 14 notice approach, DSAR process, DPIA, SCCs for transfers
- [ ] CCPA/CPRA and US state data-broker registration obligations
- [ ] UAE PDPL (Dubai entity), other target markets
- [ ] Terms of every enabled source (Google Maps Platform terms incl. caching/display, SERP vendor, registries, verifier, LLM providers) incl. storage and redistribution rights
- [ ] Customer ToS, Privacy Policy, DPA, AUP, sub-processor list, cookie policy
- [ ] Anti-spam and telemarketing rules customers must follow (TRAI/DND, CAN-SPAM, PECR, CASL) — reflected in AUP
- [ ] Data residency commitments for Indian enterprise customers
- [ ] Breach response plan and notification timelines
