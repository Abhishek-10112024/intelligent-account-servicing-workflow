# ── k8s/frontend.Dockerfile ───────────────────────────────────────────────────
# Builds the React frontend into a minimal nginx container for Kubernetes.
#
# Usage:
#   # Build from project root:
#   cd frontend && npm run build
#   docker build -f k8s/frontend.Dockerfile -t your-registry/iasw-frontend:latest .
#   docker push your-registry/iasw-frontend:latest
#
#   # Update k8s/frontend.yaml image field, then:
#   kubectl apply -f k8s/frontend.yaml

# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Install deps first (cached layer if package.json unchanged)
COPY package.json package-lock.json* ./
RUN npm ci --silent

# Copy source and build
COPY . .

# Inject the backend API URL at build time
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=${VITE_API_URL}

RUN npm run build

# ── Stage 2: Serve ────────────────────────────────────────────────────────────
FROM nginx:1.25-alpine

# Remove default nginx static content
RUN rm -rf /usr/share/nginx/html/*

# Copy React build output
COPY --from=builder /app/dist /usr/share/nginx/html

# Write nginx config inline — no external file dependency needed
RUN printf 'server {\n\
    listen 80;\n\
    server_name _;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / { try_files $uri $uri/ /index.html; }\n\
    add_header X-Frame-Options "SAMEORIGIN" always;\n\
    add_header X-Content-Type-Options "nosniff" always;\n\
    add_header X-XSS-Protection "1; mode=block" always;\n\
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {\n\
        expires 1y;\n\
        add_header Cache-Control "public, immutable";\n\
    }\n\
    location /health.txt { return 200 "ok"; add_header Content-Type text/plain; }\n\
    gzip on;\n\
    gzip_types text/plain text/css application/json application/javascript;\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD wget -qO- http://localhost/health.txt || exit 1

CMD ["nginx", "-g", "daemon off;"]
