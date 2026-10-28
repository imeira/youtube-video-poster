# Hermes approval bridge manifest

## Artifact

- `src/approval/hermes_bridge_composition.py` — explicit single-owner composition, deterministic durable-record recognizer, and authenticated local ingress binding.
- `integrations/hermes/hermes_studio_approval_bridge/plugin.py` — disabled-by-default group `-1` handler; accepts only the composition.

## Normal Hermes-loader activation path

The normal Hermes loader invokes `register(ctx)` with no application arguments. This plugin now composes the existing production Telegram gate only when its authenticated plugin settings explicitly set `approval_bridge.enabled: true`; missing, malformed, or incomplete settings return `False` and register nothing.

Put non-secret values under the plugin's Hermes settings path (`plugins.entries.<plugin-id>.settings.approval_bridge`), using the plugin key reported by `hermes plugins list`:

```yaml
approval_bridge:
  enabled: true
  studio_config_path: C:/HermesStudio/config.yaml
  journal_path: C:/HermesStudio/state/telegram-journal.sqlite3
  registry_path: C:/HermesStudio/state/telegram-deliveries.sqlite3
  chat_id: "10001"
  trusted_sender_ids: ["20002"]
  instance_id: studio-production-a
  bridge_hmac_env: STUDIO_BRIDGE_HMAC
  telegram_bot_token_env: STUDIO_TELEGRAM_BOT_TOKEN
  telegram_webhook_secret_env: STUDIO_TELEGRAM_WEBHOOK_SECRET
```

The three named environment variables must be present in the Hermes profile environment (normally its `.env`), never in this YAML. Their selector fields are fixed literals—not aliases or arbitrary uppercase names—and the bootstrap rejects `PATH`, `HOME`, provider variables, and every undocumented alias before reading the environment. `STUDIO_BRIDGE_HMAC` must differ from `STUDIO_TELEGRAM_WEBHOOK_SECRET`; equality fails before journal or registry creation. The Studio config's `approval_delivery.chat_id` and `approval_delivery.trusted_sender_ids` must exactly match this plugin configuration. All IDs must be canonical positive decimal strings; journal and registry paths must be absolute. `studio_config_path` must be an existing regular absolute file with neither itself nor any lexical ancestor a symlink or Windows reparse point; it is checked before any environment read, Studio config load, production factory call, or store write.

On successful opt-in the bootstrap loads the existing `ProductionTelegramGateConfig` / `create_production_telegram_gate` composition, retains its journal and registry bindings, and binds only `gate.service` into `compose_hermes_approval_bridge`. It supplies a deliberately dormant provider to the existing factory and a non-provider Director model-router sentinel: it constructs no LLM client, sends no media, starts no listener/poller, configures no webhook, and makes no network request. The plugin only adds the existing scoped group `-1` PTB reply handler when Hermes's Telegram adapter connects.

### Deployment prerequisites and verification

This repository change has **not** installed the plugin, written Hermes settings or secrets, restarted Hermes, or enabled a live Telegram gateway.

Before an operator activates it:

1. Make this repository importable to the Hermes plugin package and provide the usual `plugin.yaml` installation metadata for the selected distribution method.
2. Add the non-secret settings above through Hermes configuration management, with `enabled: false` (or no block) until all paths and bindings are verified.
3. Add these secret names, with values external to this repository: `STUDIO_BRIDGE_HMAC`, `STUDIO_TELEGRAM_BOT_TOKEN`, and `STUDIO_TELEGRAM_WEBHOOK_SECRET`.
4. Verify the Studio config and `STUDIO_EPISODES_DIR` resolve to the intended existing production journal/receipt root; the configured chat and sender allowlist must match both places.
5. Run the focused offline test, then enable the plugin and use `hermes plugins doctor <plugin-path>` before any controlled gateway reload. Do not start a second Telegram process, poller, webhook, or listener.

Offline verification performed by the test suite uses the real Studio gate composition, a fake PTB application, a pre-existing delivery receipt, and asserts the existing thumbnail approval creates the durable `video_delivery_outbox` `PENDING` row. It does not contact Telegram.
