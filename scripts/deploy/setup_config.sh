#!/bin/bash
# This script sets up the local configuration file from the template.

# Get the directory of the script
SCRIPT_DIR=$(dirname "$(realpath "$0")")
# Go up two levels to the project root
PROJECT_ROOT=$(dirname "$(dirname "$SCRIPT_DIR")")

CONFIG_DIR="$PROJECT_ROOT/config"
TEMPLATE_PATH="$CONFIG_DIR/config.yaml.example"
CONFIG_PATH="$CONFIG_DIR/config.yaml"

# Check if the actual config file already exists
if [ -f "$CONFIG_PATH" ]; then
    echo "Configuration file already exists at '$CONFIG_PATH'. No action taken."
else
    # Check if the template file exists before trying to copy
    if [ -f "$TEMPLATE_PATH" ]; then
        echo "Creating new configuration from template..."
        cp "$TEMPLATE_PATH" "$CONFIG_PATH"
        echo "New configuration file created: '$CONFIG_PATH'"
        echo "IMPORTANT: Please edit this file with your deployment-specific settings."
    else
        echo "ERROR: Configuration template not found at '$TEMPLATE_PATH'"
        exit 1
    fi
fi

exit 0
