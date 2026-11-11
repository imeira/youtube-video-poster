"""TEST-only verification of frozen, offline canonical fixture bytes.

This module cannot read or attest a live database.  It validates only explicitly
pinned fixture payloads for tests, returns TEST-bound evidence, and has no
issuer, dispatch, provider, credential, reservation, or production capability.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sqlite3
import stat
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LITERAL = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)


class CanonicalOperationBindingError(PermissionError):
    """A fixed canonical source did not prove the requested operation binding."""


_WINDOWS_FILE_ALL_ACCESS = 0x1F01FF
_WINDOWS_SYSTEM_SID = "S-1-5-18"
_WINDOWS_ADMINS_SID = "S-1-5-32-544"


def _windows_validate_protected_dacl_snapshot(
    snapshot: dict[str, object], expected_process_sid: str, label: str
) -> None:
    """Require the publisher's protected DACL, bound to this process token."""
    expected_aces = (
        (expected_process_sid, _WINDOWS_FILE_ALL_ACCESS, False),
        (_WINDOWS_SYSTEM_SID, _WINDOWS_FILE_ALL_ACCESS, False),
        (_WINDOWS_ADMINS_SID, _WINDOWS_FILE_ALL_ACCESS, False),
    )
    if (
        snapshot.get("owner_sid") != expected_process_sid
        or snapshot.get("dacl_protected") is not True
        or snapshot.get("aces") != expected_aces
    ):
        raise CanonicalOperationBindingError(f"{label} protected DACL is invalid")


def _windows_current_process_sid() -> str:
    """Derive the reader identity from its current Win32 process token."""
    if sys.platform != "win32":
        raise CanonicalOperationBindingError("Windows token SID is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)
    ]
    open_process_token.restype = ctypes.c_int
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_token_information.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    current_process = kernel32.GetCurrentProcess
    current_process.restype = ctypes.c_void_p

    token = ctypes.c_void_p()
    if not open_process_token(current_process(), 0x0008, ctypes.byref(token)):
        raise CanonicalOperationBindingError("Windows process token is unavailable")
    try:
        needed = ctypes.c_uint32()
        get_token_information(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise CanonicalOperationBindingError("Windows process token is unavailable")
        buffer = ctypes.create_string_buffer(needed.value)
        if not get_token_information(
            token, 1, buffer, needed.value, ctypes.byref(needed)
        ):
            raise CanonicalOperationBindingError("Windows process token is unavailable")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = ctypes.c_wchar_p()
        if not convert_sid(sid, ctypes.byref(text)) or not text.value:
            raise CanonicalOperationBindingError("Windows process SID is unavailable")
        try:
            return text.value
        finally:
            local_free(text)
    finally:
        close_handle(token)


def _windows_security_snapshot_from_handle(handle: int, label: str) -> dict[str, object]:
    """Read owner and every ACE from the same native handle used for bytes."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security_info = advapi32.GetSecurityInfo
    get_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security_info.restype = ctypes.c_uint32
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_uint32)
    ]
    get_control.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    def sid_text(sid: ctypes.c_void_p) -> str:
        text = ctypes.c_wchar_p()
        if not convert_sid(sid, ctypes.byref(text)) or not text.value:
            raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
        try:
            return text.value
        finally:
            local_free(text)

    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    result = get_security_info(
        handle, 1, 0x00000001 | 0x00000004,
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor),
    )
    if result or not owner.value or not dacl.value or not descriptor.value:
        if descriptor.value:
            local_free(descriptor)
        raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
    try:
        control, revision = ctypes.c_ushort(), ctypes.c_uint32()
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
        header = ctypes.string_at(dacl, 8)
        acl_size = int.from_bytes(header[2:4], "little")
        ace_count = int.from_bytes(header[4:6], "little")
        if acl_size < 8:
            raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
        offset, aces = 8, []
        for _ in range(ace_count):
            if offset + 12 > acl_size:
                raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
            ace = ctypes.string_at(dacl.value + offset, 12)
            ace_type, ace_flags = ace[0], ace[1]
            ace_size = int.from_bytes(ace[2:4], "little")
            if ace_type != 0 or ace_size < 12 or offset + ace_size > acl_size:
                raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
            aces.append(
                (
                    sid_text(ctypes.c_void_p(dacl.value + offset + 8)),
                    int.from_bytes(ace[4:8], "little"),
                    bool(ace_flags & 0x10),
                )
            )
            offset += ace_size
        if offset != acl_size:
            raise CanonicalOperationBindingError(f"{label} protected DACL inspection failed")
        return {
            "owner_sid": sid_text(owner),
            "dacl_protected": bool(control.value & 0x1000),
            "aces": tuple(aces),
        }
    finally:
        local_free(descriptor)


@dataclass(frozen=True)
class CanonicalOperationBinding:
    """Repository-selected paths and pins for one literal operation ID."""

    operation_id: str
    root: Path
    authority_name: str
    authority_sha256: str
    financial_name: str
    financial_sha256: str
    ledger_name: str
    transcript_path: Path
    operation_scope: str
    # Frozen TEST fixtures only; arbitrary/live filesystem capture is rejected.
    fixture_mode: str = "UNBOUND"
    expected_transcript_sha256: str = ""
    expected_ledger_sha256: str = ""


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise CanonicalOperationBindingError("duplicate JSON key")
        result[key] = value
    return result


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise CanonicalOperationBindingError(f"{label} must be a lower-case SHA-256")
    return value


def _literal(value: object, label: str) -> str:
    if not isinstance(value, str) or _LITERAL.fullmatch(value) is None:
        raise CanonicalOperationBindingError(f"literal {label} is invalid")
    return value


def _money(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise CanonicalOperationBindingError(f"{label} must be a decimal string")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise CanonicalOperationBindingError(f"{label} is invalid") from error
    if not amount.is_finite() or amount <= 0:
        raise CanonicalOperationBindingError(f"{label} is invalid")
    return value


def _no_reparse(path: Path, label: str) -> None:
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise CanonicalOperationBindingError(f"{label} traverses a reparse point")


def _identity(path: Path, label: str) -> tuple[int, int, int, int]:
    """Return a stable regular-file/dir identity without following a link."""
    _no_reparse(path, label)
    try:
        info = path.lstat()
    except OSError as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable") from error
    if stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise CanonicalOperationBindingError(f"{label} traverses a reparse point")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _require_no_sqlite_sidecars(path: Path, label: str) -> None:
    """WAL/SHM means a main-db-only snapshot could silently be stale."""
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise CanonicalOperationBindingError(f"{label} sidecar inspection failed") from error
        raise CanonicalOperationBindingError(f"{label} WAL/SHM sidecar is present")


def _stable_bytes_posix(path: Path, label: str, *, protected: bool = False) -> bytes:
    """Open an absolute path as a pinned dirfd chain; never resolve a final string."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise CanonicalOperationBindingError(f"{label} unsupported platform boundary")
    absolute = path.absolute()
    if not absolute.is_absolute() or not absolute.name:
        raise CanonicalOperationBindingError(f"{label} is unavailable")
    descriptors: list[int] = []
    try:
        current = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(current)
        for part in absolute.parent.relative_to(absolute.anchor).parts:
            current = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
            )
            descriptors.append(current)
        final = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
        descriptors.append(final)
        information = os.fstat(final)
        if not stat.S_ISREG(information.st_mode):
            raise CanonicalOperationBindingError(f"{label} is not a regular file")
        if protected and (
            information.st_uid != os.getuid()
            or stat.S_IMODE(information.st_mode) & 0o077
        ):
            raise CanonicalOperationBindingError(
                f"{label} is not an owner-only protected file"
            )
        chunks: list[bytes] = []
        while chunk := os.read(final, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _stable_bytes_windows(
    path: Path, label: str, *, protected: bool = False,
    share_delete_with_pinned_publisher: bool = False,
) -> bytes:
    """Pin every ancestor HANDLE with no delete sharing before reading the file.

    FILE_FLAG_OPEN_REPARSE_POINT applies only to each object opened, not to its
    ancestors.  Opening and retaining every directory handle (without
    FILE_SHARE_DELETE) prevents a junction swap between validation and the
    final file handle.  The sole exception is an Hermes IPC capability whose
    publisher holds the root open without FILE_SHARE_DELETE for the complete
    export lifetime: that reader shares DELETE so it can coexist with the
    publisher's DELETE-access pin while the publisher remains the rename guard.
    All validation and reads are through Win32 HANDLEs;
    ``os.open`` is deliberately never used on Windows.
    """
    if sys.platform != "win32":
        raise CanonicalOperationBindingError(f"{label} unsupported platform boundary")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    get_information.restype = ctypes.c_int
    read_file = kernel32.ReadFile
    read_file.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    read_file.restype = ctypes.c_int
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32
    ]
    get_final_path.restype = ctypes.c_uint32
    invalid = ctypes.c_void_p(-1).value
    generic_read, file_list_directory = 0x80000000, 0x0001
    share_read_write = 0x00000001 | 0x00000002
    share_mode = share_read_write | (0x00000004 if share_delete_with_pinned_publisher else 0)
    open_existing, reparse, backup_semantics = 3, 0x00200000, 0x02000000
    reparse_attribute, directory_attribute = 0x0400, 0x0010

    def verify_protected_dacl(handle: int) -> None:
        """Bind the same read handle to the intrinsic reader-token identity."""
        expected_process_sid = _windows_current_process_sid()
        snapshot = _windows_security_snapshot_from_handle(handle, label)
        _windows_validate_protected_dacl_snapshot(
            snapshot, expected_process_sid, label
        )

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", ctypes.c_uint32),
            ("creation_low", ctypes.c_uint32), ("creation_high", ctypes.c_uint32),
            ("access_low", ctypes.c_uint32), ("access_high", ctypes.c_uint32),
            ("write_low", ctypes.c_uint32), ("write_high", ctypes.c_uint32),
            ("volume_serial", ctypes.c_uint32), ("size_high", ctypes.c_uint32),
            ("size_low", ctypes.c_uint32), ("links", ctypes.c_uint32),
            ("file_index_high", ctypes.c_uint32), ("file_index_low", ctypes.c_uint32),
        ]

    def handle_information(handle: int) -> FileInformation:
        information = FileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise CanonicalOperationBindingError(f"{label} handle inspection failed")
        return information

    def normalized_handle_path(handle: int) -> str:
        size = get_final_path(handle, None, 0, 0)
        if not size:
            raise CanonicalOperationBindingError(
                f"{label} final handle path inspection failed"
            )
        buffer = ctypes.create_unicode_buffer(size + 1)
        written = get_final_path(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            raise CanonicalOperationBindingError(
                f"{label} final handle path inspection failed"
            )
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            return ("\\\\" + value[8:]).rstrip("\\").casefold()
        value = value.removeprefix("\\\\?\\")
        return value.rstrip("\\").casefold()

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ushort),
            ("maximum_length", ctypes.c_ushort),
            ("buffer", ctypes.c_wchar_p),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("root_directory", ctypes.c_void_p),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", ctypes.c_ulong),
            ("security_descriptor", ctypes.c_void_p),
            ("security_quality_of_service", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("status", ctypes.c_long), ("information", ctypes.c_void_p)]

    nt_create_file = ctypes.WinDLL("ntdll", use_last_error=True).NtCreateFile
    nt_create_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
    ]
    nt_create_file.restype = ctypes.c_long

    def open_child(parent: int, name: str, *, directory: bool) -> int:
        """Open one literal child relative to the already pinned parent HANDLE."""
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = UnicodeString(
            len(name.encode("utf-16-le")),
            (len(name) + 1) * 2,
            ctypes.cast(name_buffer, ctypes.c_wchar_p),
        )
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(unicode_name),
            0x40, None, None,  # OBJ_CASE_INSENSITIVE
        )
        handle, status = ctypes.c_void_p(), IoStatusBlock()
        options = 0x20 | reparse | (0x1 if directory else 0x40)
        result = nt_create_file(
            ctypes.byref(handle),
            ((file_list_directory | 0x80) if directory else generic_read) | 0x00100000,
            ctypes.byref(attributes), ctypes.byref(status), None, 0, share_mode,
            1, options, None, 0,
        )
        if result < 0 or handle.value == invalid:
            raise CanonicalOperationBindingError(
                f"{label} child handle is unavailable ({result:#x})"
            )
        return handle.value

    absolute = path.absolute()
    if not absolute.is_absolute() or not absolute.name:
        raise CanonicalOperationBindingError(f"{label} is unavailable")
    handles: list[int] = []
    try:
        # A volume root has no mutable parent.  Every mutable component is
        # subsequently opened relative to this retained HANDLE, never by a
        # re-resolved absolute string that a post-publisher junction can steer.
        handle = create_file(
            str(Path(absolute.anchor)), file_list_directory, share_mode, None,
            open_existing, reparse | backup_semantics, None,
        )
        if handle == invalid:
            raise CanonicalOperationBindingError(f"{label} ancestor handle is unavailable")
        handles.append(handle)
        information = handle_information(handle)
        if information.attributes & reparse_attribute:
            raise CanonicalOperationBindingError(f"{label} traverses a reparse point")
        for part in absolute.parent.relative_to(absolute.anchor).parts:
            handle = open_child(handles[-1], part, directory=True)
            handles.append(handle)
            information = handle_information(handle)
            if information.attributes & reparse_attribute:
                raise CanonicalOperationBindingError(f"{label} traverses a reparse point")
        file_handle = open_child(handles[-1], absolute.name, directory=False)
        handles.append(file_handle)
        information = handle_information(file_handle)
        file_id = (information.volume_serial, information.file_index_high, information.file_index_low)
        if (
            information.attributes & (reparse_attribute | directory_attribute)
            or file_id == (0, 0, 0)
            or normalized_handle_path(file_handle)
            != str(absolute).rstrip("\\").casefold()
        ):
            raise CanonicalOperationBindingError(f"{label} is not a regular non-reparse file")
        if protected:
            verify_protected_dacl(file_handle)
        chunks: list[bytes] = []
        while True:
            buffer = ctypes.create_string_buffer(1024 * 1024)
            count = ctypes.c_uint32()
            if not read_file(file_handle, buffer, len(buffer), ctypes.byref(count), None):
                raise CanonicalOperationBindingError(f"{label} read failed")
            if not count.value:
                break
            chunks.append(buffer.raw[: count.value])
        return b"".join(chunks)
    finally:
        for handle in reversed(handles):
            close_handle(handle)


def _stable_bytes(
    path: Path, label: str, *, protected: bool = False,
    share_delete_with_pinned_publisher: bool = False,
) -> bytes:
    """Read only via a platform-native no-follow/pinned boundary."""
    if sys.platform == "win32":
        return _stable_bytes_windows(
            path, label, protected=protected,
            share_delete_with_pinned_publisher=share_delete_with_pinned_publisher,
        )
    if os.name == "posix":
        return _stable_bytes_posix(path, label, protected=protected)
    raise CanonicalOperationBindingError(f"{label} unsupported platform boundary")


def _literal_child(root: Path, name: object, label: str) -> Path:
    if not isinstance(name, str) or not name or any(part in name for part in ("/", "\\", ":", "..")):
        raise CanonicalOperationBindingError(f"{label} is not a literal source-owned locator")
    candidate = root / name
    _no_reparse(candidate, label)
    return candidate


def _json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = _stable_bytes(path, label)
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable strict JSON") from error
    if not isinstance(document, dict):
        raise CanonicalOperationBindingError(f"{label} must be an object")
    return document, sha256(raw).hexdigest()


def _closed(document: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise CanonicalOperationBindingError(f"closed {label} schema is required")
    return document


def _read_sqlite_snapshot(path: Path, label: str) -> sqlite3.Connection:
    """Open only verifier-captured bytes; never let SQLite open source paths."""
    _require_no_sqlite_sidecars(path, label)
    raw = _stable_bytes(path, label)
    _require_no_sqlite_sidecars(path, label)
    try:
        database = sqlite3.connect(":memory:")
        database.deserialize(raw)
        return database
    except (sqlite3.Error, ValueError) as error:
        try:
            database.close()
        except UnboundLocalError:
            pass
        raise CanonicalOperationBindingError(f"{label} snapshot is unavailable") from error


def _read_transcript(path: Path, consent: dict[str, Any]) -> None:
    assistant_id = consent["adjacent_assistant_message_id"]
    user_id = consent["user_message_id"]
    if type(assistant_id) is not int or type(user_id) is not int or user_id != assistant_id + 1:
        raise CanonicalOperationBindingError("canonical transcript message IDs are not adjacent")
    try:
        database = _read_sqlite_snapshot(path, "canonical transcript")
        try:
            rows = database.execute(
                "SELECT id, session_id, role, content FROM messages WHERE id IN (?, ?) ORDER BY id",
                (assistant_id, user_id),
            ).fetchall()
        finally:
            database.close()
    except sqlite3.Error as error:
        raise CanonicalOperationBindingError("canonical transcript is unavailable") from error
    if len(rows) != 2:
        raise CanonicalOperationBindingError("canonical transcript records are incomplete")
    expected = (
        (assistant_id, "assistant", consent["assistant_message_sha256"]),
        (user_id, "user", consent["user_message_sha256"]),
    )
    for row, (message_id, role, digest) in zip(rows, expected, strict=True):
        actual_id, session_id, actual_role, content = row
        if (
            actual_id != message_id
            or session_id != consent["session_id"]
            or actual_role != role
            or not isinstance(content, str)
            or sha256(content.encode("utf-8")).hexdigest() != digest
        ):
            raise CanonicalOperationBindingError("canonical transcript binding rejected")


def _read_ledger(path: Path, financial_sha256: str) -> None:
    try:
        database = _read_sqlite_snapshot(path, "canonical financial ledger")
        try:
            row = database.execute("SELECT binding FROM policy WHERE id = 1").fetchone()
        finally:
            database.close()
        binding = json.loads(row[0], object_pairs_hook=_pairs) if row is not None else None
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CanonicalOperationBindingError("canonical financial ledger is unavailable") from error
    if not isinstance(binding, dict) or set(binding) != {"financial_policy_sha256"}:
        raise CanonicalOperationBindingError("canonical financial ledger binding is invalid")
    if binding["financial_policy_sha256"] != financial_sha256:
        raise CanonicalOperationBindingError("canonical financial ledger policy hash mismatch")


def verify_canonical_operation_binding(
    binding: CanonicalOperationBinding, *, transcript_path: Path
) -> dict[str, Any]:
    """Verify a frozen TEST fixture and return non-authoritative bound evidence.

    This function rejects non-TEST fixture contracts before any file operation.
    It intentionally does not provide live SQLite consistency: a transient or
    persistent WAL writer is outside this verifier's authority.  SQLite only
    deserializes already captured fixture bytes in memory; it never opens a
    source database through SQLite, writes, checkpoints, or deletes source state.
    """
    if not isinstance(binding, CanonicalOperationBinding):
        raise CanonicalOperationBindingError("canonical binding type is required")
    # This is intentionally not a live-source capture API. It cannot establish
    # atomicity against a WAL writer, so reject before any path inspection/open.
    if binding.fixture_mode != "TEST_OFFLINE_FROZEN":
        raise CanonicalOperationBindingError("offline TEST fixture contract is required")
    expected_transcript_sha = _sha(binding.expected_transcript_sha256, "expected transcript")
    expected_ledger_sha = _sha(binding.expected_ledger_sha256, "expected ledger")
    operation_id = _literal(binding.operation_id, "operation ID")
    operation_scope = _literal(binding.operation_scope, "operation scope")
    root = Path(binding.root)
    _no_reparse(root, "canonical root")
    policy_path = _literal_child(root, binding.authority_name, "operation authority")
    financial_path = _literal_child(root, binding.financial_name, "financial policy")
    ledger_path = _literal_child(root, binding.ledger_name, "financial ledger")
    root_identity = _identity(root, "canonical root")
    _require_no_sqlite_sidecars(Path(transcript_path), "canonical transcript")
    _require_no_sqlite_sidecars(ledger_path, "canonical financial ledger")
    # Integrity pins are TEST-fixture checks only, not live source authority.
    if sha256(_stable_bytes(Path(transcript_path), "canonical transcript")).hexdigest() != expected_transcript_sha:
        raise CanonicalOperationBindingError("canonical transcript fixture hash mismatch")
    if sha256(_stable_bytes(ledger_path, "canonical financial ledger")).hexdigest() != expected_ledger_sha:
        raise CanonicalOperationBindingError("canonical financial ledger fixture hash mismatch")
    policy, policy_sha = _json(policy_path, "operation authority")
    financial, financial_sha = _json(financial_path, "financial policy")
    if policy_sha != _sha(binding.authority_sha256, "operation authority"):
        raise CanonicalOperationBindingError("operation authority immutable hash mismatch")
    if financial_sha != _sha(binding.financial_sha256, "financial policy"):
        raise CanonicalOperationBindingError("financial policy immutable hash mismatch")
    policy = _closed(policy, {"schema", "operation_id", "episode_id", "operation_scope", "canonical_consent"}, "operation authority")
    episode_id = _literal(policy["episode_id"], "episode ID")
    if (
        policy["schema"] != "generic-canonical-operation-policy/v1"
        or policy["operation_id"] != operation_id
        or policy["operation_scope"] != operation_scope
    ):
        raise CanonicalOperationBindingError("operation authority scope is invalid")
    consent = _closed(
        policy["canonical_consent"],
        {"schema", "session_id", "adjacent_assistant_message_id", "user_message_id", "assistant_message_sha256", "user_message_sha256"},
        "canonical consent",
    )
    if consent["schema"] != "canonical-transcript-provenance/v1" or not isinstance(consent["session_id"], str) or not consent["session_id"]:
        raise CanonicalOperationBindingError("canonical consent provenance is invalid")
    _sha(consent["assistant_message_sha256"], "assistant message")
    _sha(consent["user_message_sha256"], "user message")
    _read_transcript(Path(transcript_path), consent)
    financial = _closed(financial, {"schema", "operation_id", "episode_id", "operation_scope", "currency", "maximum_usd", "status"}, "financial policy")
    maximum_usd = _money(financial["maximum_usd"], "financial maximum")
    if (
        financial["schema"] != "generic-canonical-financial-policy/v1"
        or financial["operation_id"] != operation_id
        or financial["episode_id"] != episode_id
        or financial["operation_scope"] != operation_scope
        or financial["currency"] != "USD"
        or financial["status"] != "ACTIVE"
    ):
        raise CanonicalOperationBindingError("financial policy scope is invalid")
    _read_ledger(ledger_path, financial_sha)
    _require_no_sqlite_sidecars(Path(transcript_path), "canonical transcript")
    _require_no_sqlite_sidecars(ledger_path, "canonical financial ledger")
    if _identity(root, "canonical root") != root_identity:
        raise CanonicalOperationBindingError("canonical root changed during verification")
    return {
        "schema": "generic-canonical-binding-evidence/v1",
        "operation_id": operation_id,
        "episode_id": episode_id,
        "operation_scope": operation_scope,
        "operation_policy_sha256": policy_sha,
        "financial_policy_sha256": financial_sha,
        "canonical_message_ids": {"assistant": consent["adjacent_assistant_message_id"], "user": consent["user_message_id"]},
        "financial": {"currency": "USD", "maximum_usd": maximum_usd},
        "read_only": True,
        "status": "TEST_BOUND_EVIDENCE",
        "authority": "NONE",
        "live_authority": False,
    }


def verify_fixed_canonical_operation_binding(binding: CanonicalOperationBinding) -> dict[str, Any]:
    """Use only the source-owned transcript locator fixed in a trusted binding."""
    return verify_canonical_operation_binding(binding, transcript_path=binding.transcript_path)
