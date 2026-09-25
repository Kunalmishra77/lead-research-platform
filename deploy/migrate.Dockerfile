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
# --prod=false because the build needs devDependencies -- turbo, typescript, nest, next all live
# there. Without it the install silently succeeds with a production-only tree and the build fails a
# step later looking for a compiler. NODE_ENV cannot be relied on to be unset here: Coolify injects
# an ARG declaration for every environment variable before building, and an ARG is visible to RUN
# as an environment variable, so NODE_ENV=production arrives whether or not this file asks for it.
RUN pnpm install --frozen-lockfile --prod=false

COPY . .
RUN pnpm build:packages

# Bootstrap is idempotent (schema, extensions, roles); migrate applies what is pending; seed is
# idempotent and fills sources, plans, credit rates, industries and geography.
CMD ["sh", "-c", "pnpm infra:bootstrap && pnpm db:migrate && pnpm db:seed"]
