# 02 — Tech Stack

Always install the latest stable version and record it in the lockfile. Verify APIs against current docs; do not rely on memory.

## Monorepo
- pnpm workspaces + Turborepo (TS) · uv (Python) · Node LTS · Python 3.12
- Conventional Commits, Husky + lint-staged, GitHub Actions CI

## Frontend — apps/web
| Need | Choice |
| --- | --- |
| Framework | Next.js App Router, React Server Components where useful, TypeScript strict |
| Styling | Tailwind CSS, shadcn/ui, lucide-react |
| Data fetching | TanStack Query; typed client generated from API OpenAPI spec |
| Tables | TanStack Table + TanStack Virtual |
| Forms | react-hook-form + Zod |
| Maps | MapLibre GL (free) |
| Charts | Recharts |
| Auth | Supabase Auth via `@supabase/ssr`, server-side only (browser never calls supabase.co; ADR-0002) |
| Real-time | EventSource (SSE) |
| Tests | Vitest + Testing Library, Playwright e2e |

## API — apps/api
| Need | Choice |
| --- | --- |
| Framework | NestJS (Fastify adapter) |
| ORM + migrations | Drizzle ORM + drizzle-kit (single owner of schema) |
| Validation | Zod (nestjs-zod) |
| Auth | Supabase Auth; API verifies access tokens against the project JWKS (ES256); orgs, workspaces and roles are our own tables (ADR-0002) |
| API docs | OpenAPI generated from Zod schemas |
| Redis | ioredis |
| Rate limiting | Redis token bucket |
| Payments | Razorpay (India), Stripe (international) |
| Email | Supabase Auth built-in email (dev, team members only); Resend as custom SMTP + app emails before beta |
| Tests | Vitest + Supertest; integration tests against the Supabase test project locally, Supabase CLI stack in CI |

## Workers — services/workers
| Need | Choice |
| --- | --- |
| Runtime | asyncio (uvloop on Linux only) |
| Queue | Redis Streams (redis-py asyncio) behind own small `jobs` package |
| HTTP | httpx (HTTP/2) |
| Browser | Playwright (Chromium), small pool, only when needed |
| robots.txt | protego |
| HTML parsing | selectolax; extruct (JSON-LD, microdata, OpenGraph); trafilatura (main text) |
| Normalization | phonenumbers, email-validator, tldextract (PSL), postal (libpostal) or a lighter address parser in MVP, rapidfuzz, unidecode |
| Entity resolution | rapidfuzz rules in MVP; Splink later |
| DB | SQLAlchemy Core (async, psycopg) — no ORM models, no migrations |
| Validation | Pydantic v2; types generated from `packages/contracts` (datamodel-code-generator) |
| Storage | aioboto3 (S3-compatible) |
| AI | Provider SDKs behind `app/ai/gateway.py`; LiteLLM optional |
| Tests | pytest, pytest-asyncio, respx (HTTP mocking), recorded fixtures |
| Quality | ruff, mypy |

## Data & infra
| Need | Local | Production (initial) |
| --- | --- | --- |
| Postgres | Supabase dev project (PG 17, ap-south-1) + separate test project | Supabase paid plan (ap-south-1) |
| Redis | portable Redis 8 for Windows (`pnpm redis:start`) | Redis Cloud paid / managed Redis |
| Object storage | Supabase Storage (S3 API) | Supabase Storage (R2 optional later, config only) |
| Mail | Supabase Auth built-in | Resend |
| Deploy | native processes on the dev machine (no Docker, ADR-0002) | Web on Vercel or container; API + workers as containers on 2-3 VMs or Render/Railway; Terraform later |
| Observability | Sentry dev, console logs | Sentry, Grafana Cloud (OTel), BetterStack uptime |
| Analytics | - | PostHog |

## External services (MVP)
- Google Places API (New) — discovery of local businesses
- One SERP API (Serper / SerpAPI / Brave Search API) — decide by cost test in Phase 2
- Companies House API, OpenCorporates API, SEC EDGAR — registries
- Greenhouse / Lever / Ashby / Workable public job board APIs
- Email verification API (ZeroBounce / NeverBounce / similar) — decide in Phase 4
- LLM: one small fast model for parse/extract/classify, one large model for planning/adjudication; embeddings model

## Dependency rule
Before adding any dependency: check maintenance status, licence (no AGPL in closed code without approval), bundle size (web), and write the reason in the PR/commit. Large choices need an ADR.
