#!/usr/bin/env bash
# Start the frontend on http://localhost:3000.
set -e
cd "$(dirname "$0")/frontend"
[ -f .env.local ] || cp .env.example .env.local
[ -d node_modules ] || npm install
npm run dev
