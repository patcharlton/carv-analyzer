#!/bin/bash

# CARV Analyzer - Quick Start Setup Script
# This script helps set up and check the development environment

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   CARV Analyzer - Setup Assistant${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check Python
echo -e "${YELLOW}Checking Python...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo -e "${GREEN}✓ $PYTHON_VERSION installed${NC}"
else
    echo -e "${RED}✗ Python 3 not found. Please install from python.org${NC}"
    exit 1
fi

# Check Node.js
echo -e "${YELLOW}Checking Node.js...${NC}"
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version 2>&1)
    echo -e "${GREEN}✓ Node.js $NODE_VERSION installed${NC}"
else
    echo -e "${RED}✗ Node.js not found. Please install from nodejs.org${NC}"
    exit 1
fi

# Check npm
echo -e "${YELLOW}Checking npm...${NC}"
if command -v npm &> /dev/null; then
    NPM_VERSION=$(npm --version 2>&1)
    echo -e "${GREEN}✓ npm $NPM_VERSION installed${NC}"
else
    echo -e "${RED}✗ npm not found. Please install Node.js from nodejs.org${NC}"
    exit 1
fi

echo ""

# Check backend .env file
echo -e "${YELLOW}Checking backend configuration...${NC}"
if [ -f "$SCRIPT_DIR/backend/.env" ]; then
    # Check if API key is configured
    if grep -q "ANTHROPIC_API_KEY=sk-" "$SCRIPT_DIR/backend/.env"; then
        echo -e "${GREEN}✓ Backend .env file exists with API key${NC}"
    elif grep -q "ANTHROPIC_API_KEY=your_api_key_here" "$SCRIPT_DIR/backend/.env"; then
        echo -e "${YELLOW}⚠ API key not configured in backend/.env${NC}"
        echo -e "  Please edit backend/.env and add your Anthropic API key"
        echo -e "  Get one at: https://console.anthropic.com/"
    else
        echo -e "${YELLOW}⚠ Backend .env exists but API key may not be configured${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Creating backend/.env from template...${NC}"
    cp "$SCRIPT_DIR/backend/.env.example" "$SCRIPT_DIR/backend/.env"
    echo -e "${YELLOW}  Please edit backend/.env and add your Anthropic API key${NC}"
    echo -e "  Get one at: https://console.anthropic.com/"
fi

echo ""

# Check/Install Python dependencies
echo -e "${YELLOW}Checking Python dependencies...${NC}"
cd "$SCRIPT_DIR/backend"

# Check if flask is installed
if python3 -c "import flask" &> /dev/null; then
    echo -e "${GREEN}✓ Python dependencies already installed${NC}"
else
    echo -e "${YELLOW}Installing Python dependencies...${NC}"
    pip3 install -r requirements.txt
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Python dependencies installed${NC}"
    else
        echo -e "${RED}✗ Failed to install Python dependencies${NC}"
        exit 1
    fi
fi

echo ""

# Check/Install Node dependencies
echo -e "${YELLOW}Checking frontend dependencies...${NC}"
cd "$SCRIPT_DIR/frontend"

if [ -d "node_modules" ] && [ -f "node_modules/.package-lock.json" ]; then
    echo -e "${GREEN}✓ Frontend dependencies already installed${NC}"
else
    echo -e "${YELLOW}Installing frontend dependencies...${NC}"
    npm install
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Frontend dependencies installed${NC}"
    else
        echo -e "${RED}✗ Failed to install frontend dependencies${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}   Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}To start the application:${NC}"
echo ""
echo -e "  ${BLUE}1. Start the backend (Terminal 1):${NC}"
echo -e "     cd $SCRIPT_DIR/backend"
echo -e "     python3 app/main.py"
echo ""
echo -e "  ${BLUE}2. Start the frontend (Terminal 2):${NC}"
echo -e "     cd $SCRIPT_DIR/frontend"
echo -e "     npm run dev"
echo ""
echo -e "  ${BLUE}3. Open in browser:${NC}"
echo -e "     http://localhost:3000"
echo ""

# Ask if user wants to start the servers
read -p "Would you like to see the start commands? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${YELLOW}Copy and paste these commands:${NC}"
    echo ""
    echo -e "${BLUE}Terminal 1 (Backend):${NC}"
    echo "cd \"$SCRIPT_DIR/backend\" && python3 app/main.py"
    echo ""
    echo -e "${BLUE}Terminal 2 (Frontend):${NC}"
    echo "cd \"$SCRIPT_DIR/frontend\" && npm run dev"
    echo ""
fi

echo -e "${GREEN}Happy skiing! ⛷️${NC}"
echo ""
