"""Lease-gated remote Installation Token adapter for the Host Worker."""
from __future__ import annotations

from .control_plane import ControlPlaneError
from .control_plane_http_client import ControlPlaneHttpClient


class RemoteTokenBroker:
    """Fetch one short-lived token through the authenticated cloud endpoint."""

    def __init__(self, control_plane: ControlPlaneHttpClient):
        self.control_plane = control_plane

    def get_token_for_job(
        self,
        control_plane,
        job_id: int,
        worker_token: str,
    ) -> str:
        if control_plane is not self.control_plane:
            raise ControlPlaneError("control_plane_identity_mismatch")
        if not self.control_plane.owns_worker_token(worker_token):
            raise ControlPlaneError("worker_identity_mismatch")
        return self.control_plane.request_installation_token(job_id)
