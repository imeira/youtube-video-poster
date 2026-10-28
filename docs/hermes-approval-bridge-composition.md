# Hermes approval bridge composition — acceptance

## Scope

This is an offline, disabled-by-default composition for the existing Hermes Telegram handler and Studio approval service. It starts no poller, webhook, listener, provider, installation, or deployment.

## Deployment status

**Pending host integration.** The artifact has not been installed, configured, registered by a live Hermes host, or used to deliver an approval. A host must explicitly create the existing configured service and bridge composition, then call the plugin registration path.

## Acceptance

1. A deployment creates one explicit composition only after validating canonical bridge/chat/sender bindings and a bridge HMAC distinct from the existing Studio webhook secret; failed validation creates no bridge replay database.
2. The plugin accepts that composition, not a caller-provided recognizer or transport. Its group `-1` handler normalizes only eligible Telegram replies.
3. Recognition is deterministic and non-LLM: the configured chat and sender, the replied delivery receipt, intact artifact digest, and the active Director gate must all match.
4. A recognized approval is signed over the local envelope, enters authenticated in-process ingress, invokes the existing `TelegramDirectorService`, and persists the existing thumbnail approval/outbox transition. It stops Hermes dispatch even if local delivery is ambiguous.
5. Unrelated or malformed replies, and ordinary `recognize(...) == False` results, are not recognized and continue to Hermes normal dispatch. An unexpected recognition failure after the raw sender/chat/reply boundary is conservatively consumed locally and must not reach the host core.
6. Tests use only local/in-process fakes and loopback state. They make no Telegram call and no network listener.
