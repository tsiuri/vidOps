"""
HTTP metrics exporter for Prometheus.

Provides a simple HTTP server that exposes metrics at /metrics endpoint.
Designed for distributed workers to run alongside the main worker process.
"""

from __future__ import annotations

import logging
import threading
import errno
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from prometheus_client import generate_latest, CollectorRegistry, CONTENT_TYPE_LATEST
from .metrics import WORKER_REGISTRY

logger = logging.getLogger("MetricsExporter")


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Prometheus metrics endpoint."""

    def do_GET(self) -> None:
        """Handle GET requests for metrics."""
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(generate_latest(WORKER_REGISTRY))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format: str, *args) -> None:
        """Override to use logger instead of stderr."""
        logger.debug(format % args)


class MetricsServer:
    """
    Simple HTTP server for exposing Prometheus metrics.

    Can be run in a separate thread alongside the main worker.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8888) -> None:
        """
        Initialize the metrics server.

        Args:
            host: Bind address (default: 0.0.0.0)
            port: Listen port (default: 8888)
        """
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False

    def start(self) -> None:
        """Start the metrics server in a background thread."""
        if self.is_running:
            logger.warning("Metrics server already running")
            return

        try:
            self.server = HTTPServer((self.host, self.port), MetricsHandler)
            self.is_running = True

            self.thread = threading.Thread(
                target=self._serve,
                daemon=True,
                name="MetricsExporter",
            )
            self.thread.start()

            logger.info(
                "Metrics server started on http://%s:%d/metrics",
                self.host,
                self.port,
            )
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                logger.warning(
                    "Metrics server port %s already in use; skipping metrics exporter.",
                    self.port,
                )
            else:
                logger.error("Failed to start metrics server: %s", exc)
            self.is_running = False
            self.server = None
        except Exception as e:
            logger.error("Failed to start metrics server: %s", e)
            self.is_running = False

    def stop(self) -> None:
        """Stop the metrics server gracefully."""
        if not self.is_running:
            return

        try:
            if self.server:
                self.server.shutdown()
            self.is_running = False
            logger.info("Metrics server stopped")
        except Exception as e:
            logger.error("Error stopping metrics server: %s", e)

    def _serve(self) -> None:
        """Run the server loop (called in background thread)."""
        if self.server:
            self.server.serve_forever()


# Global instance for easy access
_global_metrics_server: Optional[MetricsServer] = None


def start_metrics_server(host: str = "0.0.0.0", port: int = 8888) -> MetricsServer:
    """
    Start the global metrics server instance.

    Args:
        host: Bind address (default: 0.0.0.0)
        port: Listen port (default: 8888)

    Returns:
        The MetricsServer instance
    """
    global _global_metrics_server

    if _global_metrics_server is not None and _global_metrics_server.is_running:
        return _global_metrics_server

    _global_metrics_server = MetricsServer(host=host, port=port)
    _global_metrics_server.start()

    return _global_metrics_server


def stop_metrics_server() -> None:
    """Stop the global metrics server instance."""
    global _global_metrics_server

    if _global_metrics_server is not None:
        _global_metrics_server.stop()


def get_metrics_server() -> Optional[MetricsServer]:
    """Get the global metrics server instance (if running)."""
    return _global_metrics_server
