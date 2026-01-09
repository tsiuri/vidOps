#!/bin/bash
set -e

echo "Updating Ollama Service Limits..."

# Update Server 1 (3060) to Max 3
SERVICE1="/etc/systemd/system/ollama-server1.service"
if [ -f "$SERVICE1" ]; then
    echo "Updating $SERVICE1 (Max 3)..."
    if grep -q "OLLAMA_MAX_LOADED_MODELS" "$SERVICE1"; then
        sed -i 's/Environment="OLLAMA_MAX_LOADED_MODELS=.*"/Environment="OLLAMA_MAX_LOADED_MODELS=3"/' "$SERVICE1"
    else
        # Insert after GPU_LAYERS line
        sed -i '/Environment="OLLAMA_GPU_LAYERS=-1"/a Environment="OLLAMA_MAX_LOADED_MODELS=3"' "$SERVICE1"
    fi
else
    echo "Warning: $SERVICE1 not found."
fi

# Update Server 2 (3090) to Max 4
SERVICE2="/etc/systemd/system/ollama-server2.service"
if [ -f "$SERVICE2" ]; then
    echo "Updating $SERVICE2 (Max 4)..."
    if grep -q "OLLAMA_MAX_LOADED_MODELS" "$SERVICE2"; then
        sed -i 's/Environment="OLLAMA_MAX_LOADED_MODELS=.*"/Environment="OLLAMA_MAX_LOADED_MODELS=4"/' "$SERVICE2"
    else
        # Insert after KEEP_ALIVE line
        sed -i '/Environment="OLLAMA_KEEP_ALIVE=5m"/a Environment="OLLAMA_MAX_LOADED_MODELS=4"' "$SERVICE2"
    fi
else
    echo "Warning: $SERVICE2 not found."
fi

echo "Reloading systemd and restarting services..."
systemctl daemon-reload
systemctl restart ollama-server1 ollama-server2

echo "Done! Limits updated."
