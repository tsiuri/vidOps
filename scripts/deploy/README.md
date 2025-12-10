# VidOps Deployment Scripts

This directory contains scripts for setting up and managing the VidOps storage broker and the workers that connect to it.

## Deployment Overview

The deployment model consists of a central **Storage Broker** machine and one or more **Worker** machines.

-   The **Broker** runs the storage service and an `nginx` reverse proxy to provide a secure (HTTPS) endpoint on the local network.
-   **Workers** are configured to trust the broker's specific TLS certificate and connect to it using the hostname `broker.internal`.

## Standard Deployment Flow

1.  **On the Broker Machine:**
    -   Run `sudo bash setup_broker_nginx.sh` to install and configure `nginx` and the `storage-broker` service. This is typically a one-time setup.

2.  **On each Worker Machine:**
    -   Run `bash setup_config.sh` to create the initial `config/config.yaml` from the template.
    -   Edit `config/config.yaml` with the correct settings for the worker.
    -   Run `sudo bash worker_trust_broker.sh` to configure the worker to trust and connect to the broker machine. You will need the broker's IP address and its certificate file.

---

## Script Details

### `setup_broker_nginx.sh`

-   **Environment:** Broker
-   **Purpose:** A one-time setup script that installs `nginx`, generates a TLS certificate, and configures `nginx` as a reverse proxy for the storage broker service. It also installs and enables the `storage-broker` systemd service.
-   **Usage:** `sudo bash setup_broker_nginx.sh [--lan-ip <IP>]`

### `setup_config.sh`

-   **Environment:** Worker (or Broker if it runs worker tasks)
-   **Purpose:** Creates a local `config/config.yaml` file from the `config/config.yaml.example` template if one does not already exist. This is the first step for configuring a new machine.
-   **Usage:** `bash setup_config.sh`

### `worker_trust_broker.sh`

-   **Environment:** Worker
-   **Purpose:** Configures a worker machine to trust the storage broker. It adds a `/etc/hosts` entry to resolve `broker.internal` to the broker's IP and installs the broker's public certificate for TLS validation. It can also patch the local `config.yaml` with the correct settings.
-   **Usage:** `sudo bash worker_trust_broker.sh --lan-ip <BROKER_IP> --ca /path/to/broker-ca.pem`

### `reissue_broker_cert.sh`

-   **Environment:** Broker
-   **Purpose:** A utility script to regenerate the TLS certificate used by the `nginx` proxy. This is useful if the broker's IP address changes or the certificate expires.
-   **Usage:** `sudo bash reissue_broker_cert.sh [--lan-ip <IP>]`

### `collect_broker_diagnostics.sh`

-   **Environment:** Broker
-   **Purpose:** A troubleshooting script that collects a wide range of logs and status information from the broker machine (system info, nginx status, broker service logs, network listeners, etc.) into a timestamped folder under `logs/diagnostics/`.
-   **Usage:** `bash collect_broker_diagnostics.sh`
