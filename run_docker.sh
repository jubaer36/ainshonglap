#!/usr/bin/env bash
# Build and run the backend in Docker on http://localhost:8000.
set -e
cd "$(dirname "$0")"
docker build -f backend/Dockerfile -t ainshonglap-backend .
docker rm -f ainshonglap-api 2>/dev/null || true
docker run -d --name ainshonglap-api -p 8000:8080 --env-file backend/.env.local ainshonglap-backend
