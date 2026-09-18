#!/bin/bash
# Local development: infrastructure in Docker, API and frontend on the host.
set -euo pipefail
cd "$(dirname "$0")"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
CONDA_ENV="${CONDA_ENV:-langchain}"

echo -e "${BLUE}Starting services with Docker Compose...${NC}"
docker compose up -d --wait postgres mongodb redis

echo -e "${GREEN}✓ PostgreSQL: localhost:5432${NC}"
echo -e "${GREEN}✓ MongoDB:    localhost:27017${NC}"
echo -e "${GREEN}✓ Redis:      localhost:6379${NC}"

if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
  conda activate "$CONDA_ENV"
fi

echo -e "${BLUE}Starting backend...${NC}"
uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

echo -e "${BLUE}Starting frontend...${NC}"
(cd frontend && npm start) &
FRONTEND_PID=$!

cleanup() {
  echo -e "\n${BLUE}Stopping...${NC}"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo ""
echo -e "${GREEN}Backend:  http://localhost:8000${NC}"
echo -e "${GREEN}Frontend: http://localhost:3000${NC}"
echo "Press Ctrl+C to stop all services"
wait
