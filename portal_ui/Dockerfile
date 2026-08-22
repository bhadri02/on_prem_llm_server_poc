# portal_ui/Dockerfile
# Build context: repo root  (docker build -f portal_ui/Dockerfile .)
#
# React/Vite SPA compiled in Node builder, served by nginx:alpine.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Copy lock files first for layer caching
COPY portal_ui/package.json portal_ui/package-lock.json ./

# Install exact versions from the lock file
RUN npm ci

# Copy the rest of the source
COPY portal_ui/ ./

# Build: tsc -b && vite build → produces /app/dist
RUN npm run build

# ── Stage 2: runtime (nginx) ──────────────────────────────────────────────────
# Unprivileged variant: runs as a non-root user out of the box (no USER/chown
# steps needed) and defaults to listening on 8080 instead of 80, since binding
# port 80 requires root.
FROM nginxinc/nginx-unprivileged:alpine

# Remove the default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Minimal SPA-friendly nginx config with react-router fallback
RUN printf 'server {\n\
    listen 8080;\n\
    server_name _;\n\
\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
\n\
    location = /favicon.ico { log_not_found off; access_log off; }\n\
    location = /robots.txt  { log_not_found off; access_log off; }\n\
}\n' > /etc/nginx/conf.d/default.conf

# Copy built static assets from builder (--chown matches this base image's
# own nginx:root ownership convention for served content)
COPY --from=builder --chown=nginx:root /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
