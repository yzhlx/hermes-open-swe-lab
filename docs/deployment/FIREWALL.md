# Firewall — minimal rules (cloud Control Plane)

Scope: the cloud Control Plane server (2 CPU / 4 GB / Ubuntu 24.04). This is a
**control-plane** host — it never runs target-repo code, builds, browsers, or
the Docker sandbox. The local Worker reaches it over **outbound HTTPS only**.

## Principle

Only the minimum inbound surface is open. The Worker API is served over HTTPS
via the reverse proxy; the app itself binds loopback and is never exposed.

## Minimal `ufw` rules

```text
# Default deny
ufw default deny incoming
ufw default allow outgoing

# SSH from a management CIDR only (replace with your bastion / VPN range)
ufw allow from 203.0.113.0/24 to any port 22 proto tcp

# HTTPS for the Worker API (the only public business port)
ufw allow 443/tcp

# Optional: keep :80 open ONLY for the HTTP->HTTPS redirect.
# If your clients always use https:// you may close :80 entirely.
ufw allow 80/tcp

ufw enable
```

## What stays closed (by design)

| Port | Reason |
| --- | --- |
| `8080` (app) | Loopback only — fronted by nginx on `443`. Never published. |
| Webhook port | **No webhook is exposed in MVP-0** (webhook stays off). No inbound webhook listener exists. |
| Docker `2375/2376` | The cloud host runs **no** Docker daemon for target code. The sandbox runs on the **local** Worker. |
| `22` from `0.0.0.0` | SSH restricted to the management CIDR above. |
| Any DB port | SQLite is a local file (`runtime/events.db`); no network port. |

## Notes

- This line does **not** modify DNS, purchase certificates, or open a public
  webhook. The reverse-proxy config references cert/key paths; provision them
  out-of-band (existing domain + cert) before enabling `443`.
- If the Worker is the only client and it sits behind the same VPN/bastion, you
  may further restrict `443` to that CIDR instead of `0.0.0.0/0`.
