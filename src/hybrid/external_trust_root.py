"""Offline verification of externally configured Ed25519 QA evidence.

The trust root is a closed JSON document stored outside the package.  Evidence
never carries an authority key: the verifier selects a configured public key by
``signer_id`` and verifies the RFC 8032 Ed25519 signature over UTF-8 canonical
JSON (sorted keys, compact separators, no NaN) of every packet field except
``signature_b64``.  QA evidence is deliberately non-financial and cannot enable
LIVE dispatch; a TEST root is explicitly reported as non-LIVE authority.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.hybrid.locks import reject_reparse_path


class TrustRootVerificationError(ValueError):
    """The external trust configuration or signed evidence is unacceptable."""


_ROOT_FIELDS = {"schema", "kind", "environment", "algorithm", "version", "keys", "revoked_signers"}
_KEY_FIELDS = {"signer_id", "role", "scopes", "public_key_b64", "not_before", "not_after"}
_PACKET_FIELDS = {
    "schema", "kind", "version", "signer_id", "role", "scope", "request_id", "episode",
    "source_sha256", "assets_sha256", "policy_sha256", "issued_at", "expires_at", "nonce", "signature_b64",
}
_BINDINGS = {"request_id", "episode", "source_sha256", "assets_sha256", "policy_sha256"}
_HEX = frozenset("0123456789abcdef")
_TRUSTED_CONFIGURATION_FIELDS = {"root_path", "root_sha256", "evidence_root", "environment"}


def canonical_json(value: object) -> bytes:
    """The documented, deterministic signed-byte representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TrustRootVerificationError("JSON contains duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise TrustRootVerificationError(f"JSON non-finite number is forbidden: {value}")


def _finite_json_float(value: str) -> Decimal:
    """Reject exponent overflow before it can become an accepted JSON value."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise TrustRootVerificationError("JSON non-finite number is forbidden") from error
    # Keep a finite JSON token from becoming an unbounded Decimal that later
    # serializes or coerces as an infinity in a consumer.  The accepted range
    # deliberately matches IEEE-754 finite magnitude even though we retain
    # Decimal for exact validation downstream.
    if not parsed.is_finite() or parsed.adjusted() > 308:
        raise TrustRootVerificationError("JSON non-finite number is forbidden")
    return parsed


def load_strict_json_snapshot(
    path: Path | str, label: str, *, contained_by: Path | None = None, include_bytes: bool = False,
) -> tuple[dict[str, Any], str] | tuple[dict[str, Any], str, bytes]:
    """Strictly parse and hash one accepted byte snapshot after lexical checks.

    Path checks occur before the initial open; the parsed document and returned
    digest are both derived from that one file-handle snapshot. This cannot make
    path-name races impossible on every filesystem, but it never hashes a later
    path reopening.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        raise TrustRootVerificationError(f"{label} must be an absolute path")
    try:
        candidate = reject_reparse_path(candidate)
    except ValueError as error:
        raise TrustRootVerificationError(f"{label} rejects symlink or reparse-point paths") from error
    if contained_by is not None:
        try:
            root = reject_reparse_path(Path(contained_by))
        except ValueError as error:
            raise TrustRootVerificationError("configured evidence scope rejects symlink or reparse-point paths") from error
        if not root.is_dir():
            raise TrustRootVerificationError("configured evidence scope is not a directory")
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise TrustRootVerificationError(f"{label} is outside its configured local scope") from error
    if not candidate.is_file():
        raise TrustRootVerificationError(f"{label} is missing or not a regular file")
    descriptor = None
    try:
        before = os.stat(candidate, follow_symlinks=False)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise TrustRootVerificationError(f"{label} is missing or not a regular file")
        if not os.path.samestat(before, opened):
            raise TrustRootVerificationError(f"{label} file identity changed before open")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw_bytes = stream.read()
        after = os.stat(candidate, follow_symlinks=False)
        if not os.path.samestat(opened, after):
            raise TrustRootVerificationError(f"{label} file identity changed during read")
        raw = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except TrustRootVerificationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustRootVerificationError(f"{label} is unreadable strict JSON") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(raw, dict):
        raise TrustRootVerificationError(f"{label} must be a JSON object")
    digest = sha256(raw_bytes).hexdigest()
    if include_bytes:
        return raw, digest, raw_bytes
    return raw, digest


def load_strict_json(path: Path | str, label: str, *, contained_by: Path | None = None) -> dict[str, Any]:
    """Read a local regular JSON document through the byte-snapshot loader."""
    return load_strict_json_snapshot(path, label, contained_by=contained_by)[0]


def _require_text(value: object, label: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise TrustRootVerificationError(f"{label} must be a bounded nonempty string")
    return value


def _require_time(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise TrustRootVerificationError(f"{label} must be a nonnegative integer UTC timestamp")
    return value


def _require_hash(value: object, label: str) -> str:
    value = _require_text(value, label, limit=64)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise TrustRootVerificationError(f"{label} must be lowercase SHA-256")
    return value


def _strict_b64(value: object, label: str) -> bytes:
    text = _require_text(value, label, limit=128)
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as error:
        raise TrustRootVerificationError(f"{label} is invalid base64") from error


def _load_root(path: Path) -> tuple[dict[str, Any], str]:
    """Validate a root and retain the digest of those exact parsed bytes."""
    path = Path(path)
    package_root = Path(__file__).absolute().parents[2]
    if not path.is_absolute():
        raise TrustRootVerificationError("external trust root configuration must be an absolute file")
    try:
        path = reject_reparse_path(path)
    except ValueError as error:
        raise TrustRootVerificationError("external trust root rejects symlink or reparse-point paths") from error
    try:
        path.relative_to(package_root)
    except ValueError:
        pass
    else:
        raise TrustRootVerificationError("external trust root must be configured outside this package")
    raw, digest = load_strict_json_snapshot(path, "external trust root")
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS:
        raise TrustRootVerificationError("external trust root must use the closed schema")
    if type(raw.get("schema")) is not int or raw.get("schema") != 1 or raw.get("kind") != "EXTERNAL_SIGNER_TRUST_ROOT":
        raise TrustRootVerificationError("unsupported external trust root schema")
    if raw.get("environment") not in {"TEST", "PRODUCTION"} or raw.get("algorithm") != "Ed25519":
        raise TrustRootVerificationError("external trust root environment or algorithm is unsupported")
    _require_text(raw.get("version"), "trust root version")
    keys = raw.get("keys")
    if not isinstance(keys, list) or not keys:
        raise TrustRootVerificationError("external trust root requires configured signer keys")
    ids: set[str] = set()
    for key in keys:
        if not isinstance(key, dict) or set(key) != _KEY_FIELDS:
            raise TrustRootVerificationError("external trust root signer schema is closed")
        signer_id = _require_text(key.get("signer_id"), "signer_id")
        if signer_id in ids:
            raise TrustRootVerificationError("external trust root signer IDs must be unique")
        ids.add(signer_id)
        if key.get("role") not in {"QA", "LICENSE"}:
            raise TrustRootVerificationError("external trust root signer role is unsupported")
        scopes = key.get("scopes")
        if (
            not isinstance(scopes, list)
            or not scopes
            or any(not isinstance(scope, str) for scope in scopes)
            or len(scopes) != len(set(scopes))
            or any(scope not in {"EVIDENCE:QA", "TTS:LICENSE"} for scope in scopes)
        ):
            raise TrustRootVerificationError("external trust root signer scopes are unsupported")
        if len(_strict_b64(key.get("public_key_b64"), "public key")) != 32:
            raise TrustRootVerificationError("Ed25519 public key must be 32 bytes")
        if _require_time(key.get("not_before"), "key not_before") >= _require_time(key.get("not_after"), "key not_after"):
            raise TrustRootVerificationError("key validity window is invalid")
    revoked = raw.get("revoked_signers")
    if (
        not isinstance(revoked, list)
        or any(not isinstance(item, str) for item in revoked)
        or len(revoked) != len(set(revoked))
    ):
        raise TrustRootVerificationError("revoked_signers must be a unique string list")
    return raw, digest


@dataclass(frozen=True)
class VerifiedEvidence:
    signer_id: str
    role: str
    scope: str
    root_version: str
    provenance: str
    live_authority: bool = False


@dataclass(frozen=True)
class TrustedExternalTrustConfiguration:
    """Deployment configuration, supplied only by the application composition.

    The pin protects the configured root bytes after the deployment has chosen
    the source. It is not evidence that this process owns the configuration.
    """

    root_path: Path
    root_sha256: str
    evidence_root: Path
    environment: str

    @classmethod
    def from_mapping(cls, raw: object) -> TrustedExternalTrustConfiguration:
        if not isinstance(raw, dict) or set(raw) != _TRUSTED_CONFIGURATION_FIELDS:
            raise TrustRootVerificationError("trusted external trust configuration must use the closed schema")
        root_path, evidence_root = Path(raw["root_path"]), Path(raw["evidence_root"])
        if not root_path.is_absolute() or not evidence_root.is_absolute():
            raise TrustRootVerificationError("trusted external trust configuration requires absolute paths")
        root_sha256 = _require_hash(raw["root_sha256"], "trusted root SHA-256")
        environment = raw.get("environment")
        if environment not in {"TEST", "PRODUCTION"}:
            raise TrustRootVerificationError("trusted external trust configuration environment is unsupported")
        return cls(root_path, root_sha256, evidence_root, environment)


class EvidenceReplayGuard:
    """Caller-owned in-memory nonce guard for a bounded offline verification run."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def consume(self, signer_id: str, nonce: str) -> None:
        token = (signer_id, nonce)
        if token in self._seen:
            raise TrustRootVerificationError("evidence replay detected")
        self._seen.add(token)


class ExternalTrustRootVerifier:
    """Verify signed QA evidence against a preconfigured external public-key root."""

    def __init__(self, root_path: Path | str, *, clock, trusted_configuration: TrustedExternalTrustConfiguration | None = None) -> None:
        self.root_path = Path(root_path)
        self.clock = clock
        self.trusted_configuration = trusted_configuration
        self.root = self._load_current_root()

    @classmethod
    def from_trusted_app_configuration(cls, configuration: TrustedExternalTrustConfiguration, *, clock) -> ExternalTrustRootVerifier:
        """Compose verification from deployment configuration, never packet data."""
        if not isinstance(configuration, TrustedExternalTrustConfiguration):
            raise TrustRootVerificationError("trusted external trust configuration object is required")
        return cls(configuration.root_path, clock=clock, trusted_configuration=configuration)

    @classmethod
    def from_app_config(cls, app_config: object, *, clock) -> ExternalTrustRootVerifier:
        """Read the deployment-owned configuration channel used by the app."""
        raw = getattr(app_config, "raw", None)
        if not isinstance(raw, dict) or "external_trust_root" not in raw:
            raise TrustRootVerificationError("external trust root is NOT_CONFIGURED")
        return cls.from_trusted_app_configuration(
            TrustedExternalTrustConfiguration.from_mapping(raw["external_trust_root"]), clock=clock
        )

    def _load_current_root(self) -> dict[str, Any]:
        root, digest = _load_root(self.root_path)
        configured = self.trusted_configuration
        if configured is None:
            return root
        if root["environment"] != configured.environment:
            raise TrustRootVerificationError(
                f"configured root environment {root['environment']} does not match trusted configuration"
            )
        try:
            evidence_root = reject_reparse_path(configured.evidence_root)
        except ValueError as error:
            raise TrustRootVerificationError("configured evidence scope rejects symlink or reparse-point paths") from error
        if not evidence_root.is_dir():
            raise TrustRootVerificationError("configured evidence scope is not a directory")
        package_root = Path(__file__).absolute().parents[2]
        try:
            evidence_root.relative_to(package_root)
        except ValueError:
            pass
        else:
            raise TrustRootVerificationError("configured evidence scope must be outside this package")
        if digest != configured.root_sha256:
            raise TrustRootVerificationError("configured root pin mismatch")
        return root

    def verify(self, packet: object, *, expected: dict[str, str], replay_guard: EvidenceReplayGuard | None = None) -> VerifiedEvidence:
        root = self._load_current_root()
        if not isinstance(packet, dict) or set(packet) != _PACKET_FIELDS:
            raise TrustRootVerificationError("signed evidence must use the closed schema")
        if type(packet.get("schema")) is not int or packet.get("schema") != 1 or packet.get("kind") != "SIGNED_QA_EVIDENCE":
            raise TrustRootVerificationError("unsupported signed evidence schema")
        _require_text(packet.get("version"), "evidence version")
        signer_id = _require_text(packet.get("signer_id"), "signer_id")
        signer = next((item for item in root["keys"] if item["signer_id"] == signer_id), None)
        if signer is None:
            raise TrustRootVerificationError("unknown signer")
        if signer_id in root["revoked_signers"]:
            raise TrustRootVerificationError("signer is revoked")
        if packet.get("role") != "QA" or packet["role"] != signer["role"]:
            raise TrustRootVerificationError("evidence role is not authorized")
        if packet.get("scope") != "EVIDENCE:QA" or packet["scope"] not in signer["scopes"]:
            raise TrustRootVerificationError("evidence scope is not authorized")
        for field in _BINDINGS:
            value = packet.get(field)
            if field.endswith("_sha256"):
                _require_hash(value, field)
            else:
                _require_text(value, field)
            if expected.get(field) != value:
                raise TrustRootVerificationError(f"evidence binding mismatch: {field}")
        if set(expected) != _BINDINGS:
            raise TrustRootVerificationError("expected evidence bindings must be closed")
        issued_at = _require_time(packet.get("issued_at"), "issued_at")
        expires_at = _require_time(packet.get("expires_at"), "expires_at")
        now = self.clock()
        if type(now) not in (int, float) or not issued_at <= now < expires_at:
            raise TrustRootVerificationError("evidence is expired or not yet valid")
        if not signer["not_before"] <= now < signer["not_after"]:
            raise TrustRootVerificationError("signer key is outside its validity window")
        nonce = _require_text(packet.get("nonce"), "nonce", limit=128)
        signature = _strict_b64(packet.get("signature_b64"), "signature")
        if len(signature) != 64:
            raise TrustRootVerificationError("signature must be an Ed25519 signature")
        body = {key: value for key, value in packet.items() if key != "signature_b64"}
        try:
            Ed25519PublicKey.from_public_bytes(_strict_b64(signer["public_key_b64"], "public key")).verify(signature, canonical_json(body))
        except (InvalidSignature, ValueError) as error:
            raise TrustRootVerificationError("evidence signature is invalid") from error
        if replay_guard is not None:
            replay_guard.consume(signer_id, nonce)
        provenance = "CRYPTOGRAPHICALLY_VALID_UNAUTHENTICATED_ROOT"
        if self.trusted_configuration is not None:
            provenance = "EXTERNAL_CONFIGURATION_VALIDATED"
        # QA evidence remains non-financial and never enables LIVE dispatch.
        return VerifiedEvidence(
            signer_id, "QA", "EVIDENCE:QA", root["version"], provenance,
        )

    def verify_tts_license_declaration(
        self, packet: object, *, expected: dict[str, str]
    ) -> VerifiedEvidence:
        """Verify a source authorization declaration without asserting legal proof.

        This deliberately supplies only cryptographic evidence and configured-root
        provenance.  Consumers must retain a separate qualification decision.
        """
        required = {
            "schema", "mode", "source_sha256", "script_sha256", "audio_sha256", "timing_sha256",
            "provider_id", "voice_id", "license_source", "signer_id", "role",
            "scope", "issued_at", "expires_at", "nonce", "signature_b64",
        }
        root = self._load_current_root()
        if not isinstance(packet, dict) or set(packet) != required:
            raise TrustRootVerificationError("TTS license declaration must use the closed schema")
        if packet.get("schema") != "tts-license-declaration-v1" or packet.get("mode") not in {"PREPRODUCTION", "TEST"}:
            raise TrustRootVerificationError("unsupported TTS license declaration schema")
        if set(expected) != {"source_sha256", "script_sha256", "audio_sha256", "timing_sha256", "provider_id", "voice_id"}:
            raise TrustRootVerificationError("expected TTS license bindings must be closed")
        for field in ("source_sha256", "script_sha256", "audio_sha256", "timing_sha256"):
            value = _require_hash(packet.get(field), field)
            if expected[field] != value:
                raise TrustRootVerificationError(f"TTS license binding mismatch: {field}")
        for field in ("provider_id", "voice_id"):
            value = _require_text(packet.get(field), field)
            if expected[field] != value:
                raise TrustRootVerificationError(f"TTS license binding mismatch: {field}")
        _require_text(packet.get("license_source"), "license_source")
        signer_id = _require_text(packet.get("signer_id"), "signer_id")
        signer = next((item for item in root["keys"] if item["signer_id"] == signer_id), None)
        if signer is None or signer_id in root["revoked_signers"]:
            raise TrustRootVerificationError("TTS license signer is unknown or revoked")
        if packet.get("role") != "LICENSE" or signer["role"] != "LICENSE":
            raise TrustRootVerificationError("TTS license role is not authorized")
        if packet.get("scope") != "TTS:LICENSE" or "TTS:LICENSE" not in signer["scopes"]:
            raise TrustRootVerificationError("TTS license scope is not authorized")
        issued_at = _require_time(packet.get("issued_at"), "issued_at")
        expires_at = _require_time(packet.get("expires_at"), "expires_at")
        now = self.clock()
        if type(now) not in (int, float) or not issued_at <= now < expires_at:
            raise TrustRootVerificationError("TTS license is expired or not yet valid")
        if not signer["not_before"] <= now < signer["not_after"]:
            raise TrustRootVerificationError("TTS license signer key is outside its validity window")
        _require_text(packet.get("nonce"), "nonce", limit=128)
        signature = _strict_b64(packet.get("signature_b64"), "signature")
        if len(signature) != 64:
            raise TrustRootVerificationError("signature must be an Ed25519 signature")
        body = {key: value for key, value in packet.items() if key != "signature_b64"}
        try:
            Ed25519PublicKey.from_public_bytes(_strict_b64(signer["public_key_b64"], "public key")).verify(signature, canonical_json(body))
        except (InvalidSignature, ValueError) as error:
            raise TrustRootVerificationError("TTS license signature is invalid") from error
        provenance = "CRYPTOGRAPHICALLY_VALID_UNAUTHENTICATED_ROOT"
        if self.trusted_configuration is not None:
            provenance = "EXTERNAL_CONFIGURATION_VALIDATED"
        return VerifiedEvidence(signer_id, "LICENSE", "TTS:LICENSE", root["version"], provenance)

    def verify_audio_alignment_attestation(
        self, packet: object, *, expected: dict[str, str]
    ) -> VerifiedEvidence:
        """Verify independent hash-bound alignment evidence without qualification."""
        required = {
            "schema", "mode", "source_sha256", "script_sha256", "audio_sha256", "timing_sha256",
            "provider_id", "voice_id", "signer_id", "role", "scope", "issued_at", "expires_at",
            "nonce", "signature_b64",
        }
        bindings = {"source_sha256", "script_sha256", "audio_sha256", "timing_sha256", "provider_id", "voice_id"}
        root = self._load_current_root()
        if not isinstance(packet, dict) or set(packet) != required:
            raise TrustRootVerificationError("audio alignment attestation must use the closed schema")
        if packet.get("schema") != "audio-alignment-attestation-v1" or packet.get("mode") not in {"PREPRODUCTION", "TEST"}:
            raise TrustRootVerificationError("unsupported audio alignment attestation schema")
        if set(expected) != bindings:
            raise TrustRootVerificationError("expected audio alignment bindings must be closed")
        for field in ("source_sha256", "script_sha256", "audio_sha256", "timing_sha256"):
            value = _require_hash(packet.get(field), field)
            if expected[field] != value:
                raise TrustRootVerificationError(f"audio alignment binding mismatch: {field}")
        for field in ("provider_id", "voice_id"):
            value = _require_text(packet.get(field), field)
            if expected[field] != value:
                raise TrustRootVerificationError(f"audio alignment binding mismatch: {field}")
        signer_id = _require_text(packet.get("signer_id"), "signer_id")
        signer = next((item for item in root["keys"] if item["signer_id"] == signer_id), None)
        if signer is None or signer_id in root["revoked_signers"]:
            raise TrustRootVerificationError("audio alignment signer is unknown or revoked")
        if packet.get("role") != "QA" or signer["role"] != "QA":
            raise TrustRootVerificationError("audio alignment role is not authorized")
        if packet.get("scope") != "EVIDENCE:QA" or "EVIDENCE:QA" not in signer["scopes"]:
            raise TrustRootVerificationError("audio alignment scope is not authorized")
        issued_at = _require_time(packet.get("issued_at"), "issued_at")
        expires_at = _require_time(packet.get("expires_at"), "expires_at")
        now = self.clock()
        if type(now) not in (int, float) or not issued_at <= now < expires_at:
            raise TrustRootVerificationError("audio alignment attestation is expired or not yet valid")
        if not signer["not_before"] <= now < signer["not_after"]:
            raise TrustRootVerificationError("audio alignment signer key is outside its validity window")
        _require_text(packet.get("nonce"), "nonce", limit=128)
        signature = _strict_b64(packet.get("signature_b64"), "signature")
        if len(signature) != 64:
            raise TrustRootVerificationError("signature must be an Ed25519 signature")
        body = {key: value for key, value in packet.items() if key != "signature_b64"}
        try:
            Ed25519PublicKey.from_public_bytes(_strict_b64(signer["public_key_b64"], "public key")).verify(signature, canonical_json(body))
        except (InvalidSignature, ValueError) as error:
            raise TrustRootVerificationError("audio alignment attestation signature is invalid") from error
        provenance = "CRYPTOGRAPHICALLY_VALID_UNAUTHENTICATED_ROOT"
        if self.trusted_configuration is not None:
            provenance = "EXTERNAL_CONFIGURATION_VALIDATED"
        return VerifiedEvidence(signer_id, "QA", "EVIDENCE:QA", root["version"], provenance)
