FROM node:20-alpine

WORKDIR /app

# Install all dependencies (including dev for TypeScript build)
COPY package*.json ./
RUN npm ci

# Copy source code
COPY . .

# Build TypeScript
RUN npm run build

# Copy SQL migration files (TypeScript compiler only outputs .js)
RUN mkdir -p dist/database/migrations && cp src/database/migrations/*.sql dist/database/migrations/

# Copy non-TS config assets (e.g. rbac.yml — the RBAC role/feature config)
RUN mkdir -p dist/config && cp src/config/*.yml dist/config/

# Remove dev dependencies after build
RUN npm prune --production

# Expose port
EXPOSE 3002

# Start the application
CMD ["node", "dist/index.js"]
