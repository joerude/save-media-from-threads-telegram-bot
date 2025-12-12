#!/bin/bash
# Quick setup script

set -e

echo "🔧 Setting up Threads Telegram Bot..."

# Check Python version
python_version=$(python3 --version | awk '{print $2}' | cut -d. -f1,2)
required_version="3.12"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Python 3.12+ required. Found: $python_version"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip install -e .

# Install Playwright browsers
echo "🌐 Installing Playwright browsers..."
playwright install chromium

# Create .env if not exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cp .env.example .env
    echo "⚠️  Please edit .env and add your TELEGRAM_BOT_TOKEN"
else
    echo "✅ .env already exists"
fi

# Create cache directory
mkdir -p cache

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env and add your TELEGRAM_BOT_TOKEN"
echo "2. Run: python main.py"
