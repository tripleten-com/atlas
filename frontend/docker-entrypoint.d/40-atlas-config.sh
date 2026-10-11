#!/bin/sh
# Runs at container start (the nginx image executes /docker-entrypoint.d/*.sh).
# Writes the API base URL the browser should use into config.js. An empty VITE_API_URL
# means "same origin", which is what the Kubernetes Ingress needs.
set -e
API_URL="${VITE_API_URL:-}"
cat > /usr/share/nginx/html/config.js <<EOF
window.__ATLAS_CONFIG__ = { apiUrl: "${API_URL}" };
EOF
echo "atlas-web: config.js written with apiUrl=\"${API_URL}\""
