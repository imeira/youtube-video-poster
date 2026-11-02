"""Read-only request-scoped readiness validation for the Hermes Studio bridge."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

REQUIRED_BRIDGE_SECRET_NAMES = ("STUDIO_BRIDGE_HMAC", "STUDIO_TELEGRAM_BOT_TOKEN", "STUDIO_TELEGRAM_WEBHOOK_SECRET")
REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION_ID = "818556d97edb648f"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _env_key_counts(path: Path) -> dict[str, int]:
    counts = {name: 0 for name in REQUIRED_BRIDGE_SECRET_NAMES}
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                name = line.split("=", 1)[0].strip()
                if name in counts:
                    counts[name] += 1
    return counts


def _gateway_running() -> bool:
    try:
        result = subprocess.run(["hermes", "gateway", "status"], text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "Gateway process running" in result.stdout + result.stderr


def _request_binding(config: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(config.get("request_root", "")))
    request = _load_json(root / "request.json")
    revision = str(config.get("revision_id", ""))
    result: dict[str, Any] = {"valid": False, "request_root": str(root), "source_sha256": None}
    if request is None:
        return result | {"reason": "request_json_missing_or_invalid"}
    if request.get("request_id") != config.get("request_id"):
        return result | {"reason": "request_id_mismatch"}
    execution = request.get("execution")
    if not isinstance(execution, dict) or execution.get("active_revision") != f"revisions/{revision}":
        return result | {"reason": "active_revision_mismatch"}
    source = root / "revisions" / revision / "inputs" / "source.json"
    if not source.is_file():
        return result | {"reason": "revision_source_missing"}
    actual_hash = _sha256(source)
    if execution.get("source_sha256") != actual_hash:
        return result | {"reason": "revision_source_hash_mismatch", "source_sha256": actual_hash}
    return result | {"valid": True, "source_sha256": actual_hash, "mode": request.get("mode")}


def _financial_gate(config: dict[str, Any]) -> dict[str, Any]:
    gate = _load_json(Path(str(config.get("gate_artifact", ""))))
    if gate is None:
        return {"valid": False, "reason": "gate_artifact_missing_or_invalid"}
    if gate.get("request_hash") != config.get("request_id"):
        return {"valid": False, "reason": "gate_request_id_mismatch"}
    if gate.get("revision") != config.get("revision_id"):
        return {"valid": False, "reason": "gate_revision_id_mismatch"}
    approved = gate.get("status") == "APPROVED_USD10" and gate.get("approval_scope") == "FINANCIAL_ONLY" and gate.get("authorizes_generation") is False
    return {"valid": approved, "reason": None if approved else "gate_not_canonical_financial_approval", "status": gate.get("status"), "approval_scope": gate.get("approval_scope"), "authorizes_generation": gate.get("authorizes_generation"), "additional_approval_required": False if approved else None, "historic_command_reference_excluded_from_media_gates": "command_platform_message_id" in gate}


def _prepared_configuration(config: dict[str, Any]) -> dict[str, Any]:
    expected = {"enabled": False, "bridge_hmac_env": "STUDIO_BRIDGE_HMAC", "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN", "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET"}
    valid = all(config.get(key) == value for key, value in expected.items())
    valid = valid and config.get("request_id") == REQUEST_ID and config.get("revision_id") == REVISION_ID
    valid = valid and isinstance(config.get("chat_id"), str) and config["chat_id"].isdecimal()
    valid = valid and isinstance(config.get("trusted_sender_ids"), list) and all(isinstance(item, str) and item.isdecimal() for item in config["trusted_sender_ids"])
    return {"valid": valid, "config_key": "plugins.entries.hermes-studio-approval-bridge.settings.approval_bridge", **expected, "registration_policy": "one pre-core handler on Hermes's existing Telegram application; no poller, webhook, or outbound sender"}


def build_report(*, config: dict[str, Any], hermes_home: Path) -> dict[str, Any]:
    secret_counts = _env_key_counts(hermes_home / ".env")
    plugin_dir = hermes_home / "plugins" / "hermes-studio-approval-bridge"
    request_binding = _request_binding(config)
    financial_gate = _financial_gate(config)
    prepared = _prepared_configuration(config)
    return {"schema": "hermes-studio-approval-bridge-readiness/2", "profile": "default", "mode": "PREPARED_DISABLED", "scope": {"request_id": config.get("request_id"), "revision_id": config.get("revision_id"), "story": "Jacó — Gênesis 28"}, "activation": {"enabled": False, "hotload_attempted": False, "gateway_restart_attempted": False, "pollers_started": 0, "messages_sent": 0, "provider_calls": 0, "financial_authority_minted": False}, "single_inbound_owner": {"existing_hermes_gateway_running": _gateway_running(), "bridge_plugin_installed": plugin_dir.is_dir(), "bridge_plugin_enabled": False, "second_poller_started": False}, "prepared_configuration": prepared, "bridge_secret_key_counts": secret_counts, "request_binding": request_binding, "financial_gate": financial_gate, "future_media_gates": {"final_jaco_video_exists": False, "provider_readback_required_when_a_future_media_delivery_exists": True, "deployment_blocking": False, "reason": "No final Jacó video exists; future delivery readback is not an installation prerequisite."}, "activation_prerequisites": {"dedicated_bridge_secrets_present_exactly_once": all(count == 1 for count in secret_counts.values()), "request_binding_valid": request_binding["valid"], "financial_gate_valid": financial_gate["valid"], "prepared_configuration_valid": prepared["valid"], "plugin_installation_pending": not plugin_dir.is_dir(), "controlled_restart_required_after_install": True}, "pending": ["Install the committed immutable Git source with the documented --ref <40-character-commit> --no-enable action.", "A single human-controlled gateway restart is required after installation because the loader has no hot-load readback contract."]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, default=Path(os.environ["HERMES_HOME"]))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = _load_json(args.config)
    if config is None:
        raise SystemExit("readiness config must be a JSON object")
    report = build_report(config=config, hermes_home=args.hermes_home)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"mode": report["mode"], "request_binding_valid": report["request_binding"]["valid"], "financial_gate_valid": report["financial_gate"]["valid"], "future_media_deployment_blocking": False, "activation_ready": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())