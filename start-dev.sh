#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting services with Docker Compose...${NC}"
docker-compose up -d

echo -e "${BLUE}Waiting for services to be healthy...${NC}"
sleep 5

echo -e "${GREEN}✓ PostgreSQL: localhost:5432${NC}"
echo -e "${GREEN}✓ MongoDB: localhost:27017${NC}"
echo -e "${GREEN}✓ Redis: localhost:6379${NC}"

echo ""
echo -e "${BLUE}Starting Backend...${NC}"
cd "$(dirname "$0")"

# Source conda properly
eval "$(conda shell.bash hook)"
conda activate langchain

uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

echo -e "${GREEN}Backend PID: $BACKEND_PID${NC}"

echo ""
echo -e "${BLUE}Starting Frontend...${NC}"
cd frontend
npm start &
FRONTEND_PID=$!

echo -e "${GREEN}Frontend PID: $FRONTEND_PID${NC}"

echo ""
echo -e "${GREEN}Services started:${NC}"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for interruption
wait