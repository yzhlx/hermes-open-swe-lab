"""Hermes Open SWE — deployment assets (line C: d3-deployment).

This package contains ONLY deployment-time infrastructure:

- ``deploy.cloud``  — cloud Control Plane runnable (health + safety wrapper
  around the Line-A state machine, which lives in ``hermes_worker``).
- ``deploy.worker`` — local Worker runner (safety wrapper around the Line-A
  ``hermes_worker.HermesWorker`` client).

It does NOT implement the D3 core state machine or the relay model adapter.
Those remain owned by Line A and are consumed here as libraries only.
"""
