# 14 — Integrations and Exports

## Export engine
- Job: `exports` row -> envelope on `jobs:export` -> worker streams leads by cursor (5,000 rows per chunk) -> writer -> S3 multipart -> signed URL (48 h) -> notify (in-app + email).
- Selection: explicit IDs, list ID, or saved filter; snapshot IDs at job start.
- Column presets: Basic, Full, With sources (adds source_url + observed_at per field), With confidence.
- Writers:
  - CSV: UTF-8 BOM, RFC 4180, formula-injection escaping, split files at 1M rows, gzip > 100 MB.
  - XLSX: xlsxwriter constant_memory / openpyxl write-only; header styling, freeze header, auto filter; new sheet every 1,000,000 rows.
  - JSON: NDJSON streaming with nested provenance.
  - Word (docx) and PDF: for reports and small lead sets (<= 2,000 rows), later phases.
- Resumable: checkpoint `cursor` after each chunk; retry continues from checkpoint.
- Audit log entry: user, rows, columns, destination.

## Google Sheets
1. OAuth 2.0 authorization code + PKCE, `access_type=offline`, `prompt=consent` on first connect.
2. Scopes: `openid email` + `https://www.googleapis.com/auth/drive.file` (files created by app or picked with Google Picker). Avoid full `drive` scope. Check whether current Google policy requires app verification for chosen scopes; plan time for it.
3. Store refresh token encrypted (envelope encryption); cache access token in Redis until expiry.
4. Create: `spreadsheets.create` (title, first sheet). Existing: Google Picker -> spreadsheet ID.
5. Worksheet per export (`Leads YYYY-MM-DD HHmm`) via `batchUpdate addSheet`; freeze row 1; basic filter; column widths.
6. Headers row + hidden `lead_id` column; persist `sheet_syncs(spreadsheet_id, sheet_id, column_map, last_row)`.
7. Write in batches (2,000-5,000 rows, payload < ~2 MB) using `values.append` with `valueInputOption=RAW`; one writer per spreadsheet at a time.
8. Update mode: read `lead_id` column, diff, `values.batchUpdate` changed ranges, append new rows.
9. Quotas: token bucket per Google user + per project; on 429 / RESOURCE_EXHAUSTED -> exponential backoff with jitter (max 6 attempts). Check current quota numbers in Google docs.
10. Limits: spreadsheet cell cap (10M cells) -> create continuation spreadsheet and link it in the first sheet.
11. Errors: `invalid_grant` -> mark integration disconnected, notify; sheet deleted -> recreate or fail clearly; partial write -> resume from `last_row`.

## CRM adapters (Phase 9)
```python
class CrmAdapter(ABC):
    provider: ClassVar[str]
    async def list_fields(self, obj: str) -> list[CrmField]: ...
    async def find_existing(self, leads: list[LeadExport]) -> dict[str, str]: ...  # lead_id -> remote_id
    async def upsert_companies(self, rows: list[MappedRow]) -> list[SyncResult]: ...
    async def upsert_people(self, rows: list[MappedRow]) -> list[SyncResult]: ...
    async def associate(self, pairs: list[tuple[str, str]]) -> None: ...
    async def fetch_statuses(self, remote_ids: list[str]) -> dict[str, str]: ...
```
| CRM | Auth | Objects | Bulk | Dedupe key |
| --- | --- | --- | --- | --- |
| HubSpot | OAuth app | companies, contacts, notes | batch upsert (100) | domain / email |
| Zoho CRM | OAuth (region data centres .in/.com/.eu) | Accounts, Contacts, Leads | bulk write | website / email |
| Pipedrive | OAuth | organizations, persons | per-request with rate limits | name+domain / email |
| Salesforce | OAuth Connected App | Account, Contact, Lead | Bulk API 2.0 (> 2k), Composite (small) | website / email |

- Mapping UI: auto-map standard fields, custom mapping saved as profile, 5-row preview.
- Conflict strategy per push: skip existing / fill empty fields / overwrite.
- Always push source info: custom properties `lf_score`, `lf_confidence`, `lf_researched_at`, `lf_source_urls` + a note with evidence links.
- `crm_sync_records` for idempotency and "in CRM" badges; failures retried; per-row error shown in UI.
