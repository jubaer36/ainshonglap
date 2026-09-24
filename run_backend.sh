#!/usr/bin/env bash
# Start the backend on http://localhost:8000.
set -e
cd "$(dirname "$0")/backend"
eval "$(conda shell.bash hook)"
conda activate ainshonglap 2>/dev/null || {
  conda create -y -n ainshonglap python=3.12
  conda activate ainshonglap
  pip install -r requirements.txt
}
[ -f .env.local ] || { cp .env.example .env.local; echo "Fill in backend/.env.local, then run again."; exit 1; }
uvicorn app.main:app --reload
