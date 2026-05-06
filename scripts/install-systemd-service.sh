#!/bin/bash
################################################################################
# Install Distributed Analysis Worker Systemd Service
#
# Purpose: Deploy the systemd service file and configure the worker
# Usage:   sudo bash scripts/install-systemd-service.sh [--uninstall] [OPTIONS]
#
# This script:
#   1. Validates prerequisites (sudo, python3, systemd)
#   2. Copies service file to /etc/systemd/system/
#   3. Creates optional environment configuration file
#   4. Enables auto-start on boot
#   5. Starts the service
#   6. Verifies everything is working
#
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE_FILE="$PROJECT_ROOT/analysis-distributed-worker.service"
SYSTEMD_DIR="/etc/systemd/system"
ENV_FILE="/etc/vidops/analysis-worker.env"
USER_ENV_FILE="$HOME/.vidops/analysis-worker.env"

# Flags
UNINSTALL=false
NO_START=false
NO_ENABLE=false
CREATE_ENV=false
VERBOSE=false

################################################################################
# Functions
################################################################################

log_info() {
    echo -e "${BLUE}ℹ${NC} $*"
}

log_success() {
    echo -e "${GREEN}✓${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $*"
}

log_error() {
    echo -e "${RED}✗${NC} $*"
}

die() {
    log_error "$@"
    exit 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root (use: sudo bash $0)"
    fi
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check python3
    if ! command -v python3 &> /dev/null; then
        die "python3 is not installed"
    fi
    log_success "python3 found"

    # Check systemctl
    if ! command -v systemctl &> /dev/null; then
        die "systemd is not available"
    fi
    log_success "systemd found"

    # Check service file exists
    if [[ ! -f "$SERVICE_FILE" ]]; then
        die "Service file not found: $SERVICE_FILE"
    fi
    log_success "Service file found"

    # Check vidops project
    if [[ ! -f "$PROJECT_ROOT/vo_cli.py" ]]; then
        die "vidops project not found at: $PROJECT_ROOT"
    fi
    log_success "vidops project found at $PROJECT_ROOT"
}

show_usage() {
    cat << EOF
Usage: sudo bash $0 [OPTIONS]

Install the distributed analysis worker as a systemd service.

OPTIONS:
    --help              Show this help message
    --uninstall         Remove the service (stop, disable, remove files)
    --no-start          Install but don't start the service
    --no-enable         Install but don't enable auto-start on boot
    --create-env        Create environment configuration file
    --user-env          Create per-user environment file (~/.vidops/)
    --verbose           Verbose output
    --machine-alias=<name>
                        Set custom machine alias (default: hostname-analysis-0)
    --ollama-url=<url>  Set Ollama URL (default: http://localhost:11434)
    --ollama-model=<model>
                        Set Ollama model (default: qwen2.5:7b-instruct)

EXAMPLES:
    # Basic installation
    sudo bash scripts/install-systemd-service.sh

    # Install with environment configuration
    sudo bash scripts/install-systemd-service.sh --create-env

    # Install with custom settings
    sudo bash scripts/install-systemd-service.sh \
        --machine-alias my-gpu-server \
        --ollama-url http://gpu-server:11434 \
        --create-env

    # Uninstall service
    sudo bash scripts/install-systemd-service.sh --uninstall

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --help)
                show_usage
                exit 0
                ;;
            --uninstall)
                UNINSTALL=true
                shift
                ;;
            --no-start)
                NO_START=true
                shift
                ;;
            --no-enable)
                NO_ENABLE=true
                shift
                ;;
            --create-env)
                CREATE_ENV=true
                shift
                ;;
            --user-env)
                CREATE_ENV=true
                USER_ENV=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --machine-alias=*)
                MACHINE_ALIAS="${1#*=}"
                shift
                ;;
            --ollama-url=*)
                OLLAMA_URL="${1#*=}"
                shift
                ;;
            --ollama-model=*)
                OLLAMA_MODEL="${1#*=}"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

uninstall_service() {
    log_info "Uninstalling systemd service..."

    SERVICE_NAME="analysis-distributed-worker.service"

    # Stop service if running
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "Stopping $SERVICE_NAME..."
        systemctl stop "$SERVICE_NAME" || true
        log_success "Service stopped"
    fi

    # Disable service
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        log_info "Disabling $SERVICE_NAME from auto-start..."
        systemctl disable "$SERVICE_NAME" || true
        log_success "Service disabled"
    fi

    # Remove service file
    if [[ -f "$SYSTEMD_DIR/$SERVICE_NAME" ]]; then
        log_info "Removing $SYSTEMD_DIR/$SERVICE_NAME..."
        rm -f "$SYSTEMD_DIR/$SERVICE_NAME"
        log_success "Service file removed"
    fi

    # Reload systemd daemon
    log_info "Reloading systemd daemon..."
    systemctl daemon-reload
    log_success "Systemd daemon reloaded"

    log_success "Service uninstalled successfully"
}

install_service() {
    log_info "Installing systemd service..."

    # Copy service file
    log_info "Copying service file to $SYSTEMD_DIR/..."
    cp "$SERVICE_FILE" "$SYSTEMD_DIR/"
    log_success "Service file copied"

    # Reload systemd daemon to recognize new service
    log_info "Reloading systemd daemon..."
    systemctl daemon-reload
    log_success "Systemd daemon reloaded"

    log_success "Service installed"
}

create_environment_file() {
    if [[ "$USER_ENV" == "true" ]]; then
        target_env="$USER_ENV_FILE"
        target_dir="$(dirname "$target_env")"
    else
        target_env="$ENV_FILE"
        target_dir="$(dirname "$target_env")"
    fi

    # Create directory if needed
    if [[ ! -d "$target_dir" ]]; then
        log_info "Creating directory: $target_dir"
        mkdir -p "$target_dir"
        if [[ "$USER_ENV" != "true" ]]; then
            chmod 755 "$target_dir"
        fi
        log_success "Directory created"
    fi

    # Check if file exists
    if [[ -f "$target_env" ]]; then
        log_warning "Environment file already exists: $target_env"
        log_info "Skipping creation (edit manually if needed)"
        return
    fi

    # Determine values
    local machine_alias="${MACHINE_ALIAS:-$(hostname)-analysis-0}"
    local ollama_url="${OLLAMA_URL:-http://localhost:11434}"
    local ollama_model="${OLLAMA_MODEL:-qwen2.5:7b-instruct}"

    # Create environment file
    log_info "Creating environment file: $target_env"
    cat > "$target_env" << EOF
# VidOps Distributed Analysis Worker Environment Configuration
# Generated by install-systemd-service.sh on $(date)

# Ollama Configuration
OLLAMA_URL=$ollama_url
OLLAMA_MODEL=$ollama_model

# Worker Configuration
MACHINE_ALIAS=$machine_alias
ANALYSIS_CAPABILITIES=$ollama_model,gpu_8gb
ANALYSIS_LEASE_MINUTES=60

# Notes:
# - Edit this file to customize worker behavior
# - Changes take effect on next service restart
# - Use: sudo systemctl restart analysis-distributed-worker
EOF

    # Set permissions
    if [[ "$USER_ENV" != "true" ]]; then
        chmod 644 "$target_env"
    else
        chmod 600 "$target_env"
    fi

    log_success "Environment file created: $target_env"
    log_info "Edit as needed, then restart service:"
    log_info "  sudo systemctl restart analysis-distributed-worker"
}

enable_service() {
    if [[ "$NO_ENABLE" == "true" ]]; then
        log_warning "Skipping auto-start enablement (--no-enable)"
        return
    fi

    log_info "Enabling service to start on boot..."
    systemctl enable analysis-distributed-worker
    log_success "Service enabled for auto-start"
}

start_service() {
    if [[ "$NO_START" == "true" ]]; then
        log_warning "Skipping service start (--no-start)"
        return
    fi

    log_info "Starting service..."
    systemctl start analysis-distributed-worker
    log_success "Service started"
}

verify_installation() {
    log_info "Verifying installation..."

    # Check if service is loaded
    if systemctl list-unit-files | grep -q "analysis-distributed-worker.service"; then
        log_success "Service registered with systemd"
    else
        log_error "Service not found in systemd"
        return 1
    fi

    # Check if running
    if systemctl is-active --quiet analysis-distributed-worker; then
        log_success "Service is running"

        # Show status
        log_info "Service status:"
        systemctl status analysis-distributed-worker --no-pager | sed 's/^/  /'
    else
        log_warning "Service is not running (this is OK if --no-start was used)"
    fi

    # Show how to view logs
    log_info "View logs with:"
    log_info "  sudo journalctl -u analysis-distributed-worker -f"

    return 0
}

show_summary() {
    cat << EOF

${GREEN}╔════════════════════════════════════════════════════════════╗${NC}
${GREEN}║   Installation Complete!                                  ║${NC}
${GREEN}╚════════════════════════════════════════════════════════════╝${NC}

${BLUE}Service:${NC}
  Location:  /etc/systemd/system/analysis-distributed-worker.service
  Status:    $(systemctl is-active analysis-distributed-worker || echo "not running")
  Enabled:   $(systemctl is-enabled analysis-distributed-worker && echo "yes" || echo "no")

${BLUE}Usage:${NC}
  Start:     sudo systemctl start analysis-distributed-worker
  Stop:      sudo systemctl stop analysis-distributed-worker
  Restart:   sudo systemctl restart analysis-distributed-worker
  Status:    sudo systemctl status analysis-distributed-worker
  Logs:      sudo journalctl -u analysis-distributed-worker -f

${BLUE}Multiple Instances:${NC}
  Start 3 workers: sudo systemctl start analysis-distributed-worker@{0,1,2}
  Stop all:        sudo systemctl stop analysis-distributed-worker@*
  Status all:      sudo systemctl status analysis-distributed-worker@*

${BLUE}Configuration:${NC}
  Environment file: $(test -f "$ENV_FILE" && echo "$ENV_FILE (exists)" || echo "$ENV_FILE (not created)")
  Edit:             sudo nano $ENV_FILE
  Apply changes:    sudo systemctl restart analysis-distributed-worker

${BLUE}Documentation:${NC}
  Service architecture:  docs/SYSTEMD_SERVICE_ARCHITECTURE.md
  Deployment guide:      SYSTEMD_DEPLOYMENT_GUIDE.md
  Development workflow:  docs/DEVELOPMENT_WORKFLOW.md

${BLUE}Next Steps:${NC}
  1. Monitor logs: sudo journalctl -u analysis-distributed-worker -f
  2. Verify tasks are being claimed: SELECT * FROM analysis_tasks;
  3. Scale workers as needed: sudo systemctl start analysis-distributed-worker@{1,2,...}

${BLUE}Uninstall:${NC}
  sudo bash scripts/install-systemd-service.sh --uninstall

EOF
}

show_uninstall_summary() {
    cat << EOF

${GREEN}╔════════════════════════════════════════════════════════════╗${NC}
${GREEN}║   Uninstallation Complete!                                ║${NC}
${GREEN}╚════════════════════════════════════════════════════════════╝${NC}

${BLUE}Service Removed:${NC}
  The systemd service has been uninstalled.

${BLUE}To Reinstall:${NC}
  sudo bash scripts/install-systemd-service.sh

${BLUE}To Run from CLI (Development):${NC}
  cd ~/bq_netservices/vidops
  python3 vo_cli.py worker start analysis-distributed

EOF
}

################################################################################
# Main
################################################################################

main() {
    # Parse arguments
    parse_args "$@"

    # Check if running as root
    check_root

    # Show header
    echo ""
    log_info "VidOps Distributed Analysis Worker - Systemd Service Installer"
    echo ""

    # Handle uninstall
    if [[ "$UNINSTALL" == "true" ]]; then
        uninstall_service
        show_uninstall_summary
        exit 0
    fi

    # Check prerequisites
    check_prerequisites

    # Install service
    install_service

    # Create environment file if requested
    if [[ "$CREATE_ENV" == "true" ]]; then
        create_environment_file
    fi

    # Enable auto-start
    enable_service

    # Start service
    start_service

    # Verify
    verify_installation || die "Verification failed"

    # Show summary
    show_summary

    log_success "Installation complete!"
}

# Run main
main "$@"
