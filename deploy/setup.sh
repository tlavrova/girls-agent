#!/usr/bin/env bash
# One-shot install / update on a fresh Ubuntu server (e.g. Oracle Cloud Always Free).
# Usage: bash setup.sh   (run it again later to update to the latest code)
set -euo pipefail

REPO=https://github.com/tlavrova/girls-agent.git
DIR="$HOME/girls-agent"

if ! command -v docker >/dev/null || ! command -v git >/dev/null; then
    sudo apt-get update -q
    sudo apt-get install -y -q docker.io git
    sudo systemctl enable --now docker
fi

if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
    cp .env.example .env
    read -rsp "Paste BOT_TOKEN from @BotFather: " token; echo
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${token}|" .env
    chmod 600 .env
fi

sudo docker build -q -t girls-agent .
sudo docker rm -f girls-agent >/dev/null 2>&1 || true
sudo docker run -d --name girls-agent --restart unless-stopped \
    --env-file .env -v girls-agent-data:/data girls-agent
sleep 5
sudo docker logs --tail 20 girls-agent
