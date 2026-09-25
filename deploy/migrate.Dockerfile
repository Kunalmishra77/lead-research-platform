# A one-shot container that applies database migrations and seeds.
#
# Separate from the API on purpose. Migrations run as the table owner, which the API role is not,
# and they must run exactly once per deploy rather than on every container start — an API that
# migrated on boot would race with itself the moment there were two of them.
#
# In Coolify: run this as a pre-deploy command or a one-off job, with DATABASE_URL_MIGRATIONS set.

FROM node:24-alpine
RUN corepack enable
WORKDIR /repo

COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY apps/api/package.json apps/api/
COPY db/package.json db/
COPY packages/config/package.json packages/config/
COPY packages/contracts/package.json packages/contracts/
COPY packages/dev-dns/package.json packages/dev-dns/
COPY packages/observability/package.json packages/observability/
COPY infra/setup/package.json infra/setup/
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build:packages

# Bootstrap is idempotent (schema, extensions, roles); migrate applies what is pending; seed is
# idempotent and fills sources, plans, credit rates, industries and geography.
CMD ["sh", "-c", "pnpm infra:bootstrap && pnpm db:migrate && pnpm db:seed"]
