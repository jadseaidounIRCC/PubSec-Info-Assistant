#!/bin/bash

set -e  # Exit on error

# Extract output.tar.gz to /home/site/wwwroot
echo "Extracting output.tar.gz..."
tar -xzf /home/site/wwwroot/output.tar.gz -C /home/site/wwwroot

# List contents of frontend directory for debugging
echo "Contents of /home/site/wwwroot/app/frontend/:"
ls -la /home/site/wwwroot/app/frontend/

# Verify Node.js is available
echo "Checking Node.js version..."
if ! command -v node >/dev/null 2>&1; then
    echo "Node.js not found. Please ensure Node.js is pre-installed in the runtime stack."
    exit 1
else
    node --version
    npm --version
fi

# Check Node.js version compatibility (Vite requires Node.js 18+)
NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo "Error: Node.js version $NODE_VERSION is too old. Vite requires Node.js 18 or higher."
    exit 1
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r /home/site/wwwroot/app/backend/requirements.txt

# Build the frontend
echo "Building frontend..."
cd /home/site/wwwroot/app/frontend
npm install || { echo "npm install failed"; exit 1; }
npm run build || { echo "npm run build failed"; exit 1; }

echo "Frontend build completed successfully."