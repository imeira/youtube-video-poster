"""Compatibility import coverage for the authenticated approval boundary."""

from src.approval.controller import (
    ApprovalController,
    ApprovalControllerError,
    approval_command,
)


def test_approval_command_is_derived_from_gate_identity():
    assert approval_command(episode_id="EP8", artifact_kind="thumbnail") == "APROVAR THUMBNAIL EP8"
    assert ApprovalController
    assert ApprovalControllerError