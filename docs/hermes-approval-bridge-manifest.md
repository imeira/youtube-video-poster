# Hermes approval bridge manifest

## Artifact

- `src/approval/hermes_bridge_composition.py` — explicit single-owner composition, deterministic durable-record recognizer, and authenticated local ingress binding.
- `integrations/hermes/hermes_studio_approval_bridge/plugin.py` — disabled-by-default group `-1` handler; accepts only the composition.

## Deployment inputs (not installed or read by this artifact)

**Deployment is pending.** This repository change does not install the plugin, supply runtime configuration, register it in a live Hermes process, or activate Telegram delivery.

1. An already-created `TelegramDirectorService` bound to the existing Director, journal, delivery registry, and outbound receipt store.
2. `HermesBridgeConfig` with an explicit instance ID, canonical configured chat ID, canonical allowlisted sender IDs, and a dedicated bridge HMAC key.
3. The bridge HMAC must differ from `TelegramDirectorService`'s existing webhook secret.
4. The host must explicitly call `compose_hermes_approval_bridge(...)`, then `register(ctx, bridge=...)`.

No poller, webhook, provider, listener, config installation, restart, secret loading, or provider call is included.
