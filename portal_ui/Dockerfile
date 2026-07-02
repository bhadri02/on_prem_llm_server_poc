# Portal UI — Layer 10 Portal_UI (POC)
# Multi-stage build: compiles the React/Vite SPA in a Node builder stage then
# serves the static dist/ output with nginx:alpine.
# nginx runs as the built-in non-root nginx user (UID 101).
# Includes SPA fallback so react-router deep links resolve correctly.
#
# Build context must be the portal_ui/ directory (or adjust COPY paths if
# building from the repo root with -f portal_ui/Dockerfile portal_ui/):
#   docker build -t portal-ui:latest portal_ui/

# ---------------------------------------------------------------------------
# Stage 1 — builder: compile the React/Vite application
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy lock files first to take advantage of Docker layer caching
COPY package.json package-lock.json ./

# Install exact versions from the lock file — no network surprises
RUN npm ci

# Copy the rest of the source and build
COPY . .

# Produces /app/dist (tsc -b && vite build)
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — final: serve static assets with nginx
# ---------------------------------------------------------------------------
FROM nginx:alpine

# Remove the default nginx configuration
RUN rm /etc/nginx/conf.d/default.conf

# Write a minimal SPA-friendly nginx config:
#   - Serves files from the standard html root
#   - Falls back to index.html for any path not matching a real file (react-router)
#   - Listens on port 80 as the nginx user (non-root)
RUN printf 'server {\n\
    listen 80;\n\
    server_name _;\n\
\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # SPA fallback: unknown paths → index.html so react-router handles routing\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
\n\
    # Disable access log for health probes to reduce noise (optional)\n\
    location = /favicon.ico { log_not_found off; access_log off; }\n\
    location = /robots.txt  { log_not_found off; access_log off; }\n\
}\n' > /etc/nginx/conf.d/default.conf

# Copy built static assets from the builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx:alpine already has a non-root "nginx" user (UID 101).
# The main nginx process starts as root to bind port 80 then drops privileges
# to the worker user automatically — this is nginx:alpine's standard behaviour.
# For strict non-root enforcement (no root at any point), switch to port 8080:
#   USER nginx
# That requires additional nginx config tweaks (pid path, etc.) which are
# beyond POC scope, so we keep the standard nginx:alpine pattern here.

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
