import asyncio
from email.parser import BytesParser
from email.policy import default

import aiohttp
import pytest

from src.providers.notification.telegram_provider import TelegramNotificationProvider


async def _loopback_response(response: bytes, request_body: list[bytes]):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        headers = {}
        for line in head.decode("iso-8859-1").split("\r\n")[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        request_body.append(head + await reader.readexactly(int(headers["content-length"])))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(response)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + response
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}/botfake"


def test_chunked_response_is_accepted_and_oversized_header_fails_closed_over_loopback():
    async def exercise(response: bytes) -> int:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(line for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:"))
            await reader.readexactly(int(length.split(b":", 1)[1]))
            writer.write(response)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        provider = TelegramNotificationProvider("fake", "123")
        provider.api_base = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/botfake"
        try:
            return await provider.send_message("123", "test")
        finally:
            server.close()
            await server.wait_closed()

    body = b'{"ok":true,"result":{"message_id":42}}'
    chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + f"{len(body):X}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    assert asyncio.run(exercise(chunked)) == 42
    excessive_header = b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * 9000 + b"\r\nContent-Length: 2\r\n\r\n{}"
    with pytest.raises(aiohttp.ClientResponseError):
        asyncio.run(exercise(excessive_header))


@pytest.mark.parametrize("kind,extension,mime", [("photo", ".png", "image/png"), ("video", ".mp4", "video/mp4")])
def test_real_multipart_bytes_over_loopback_transport(tmp_path, kind, extension, mime):
    path = tmp_path / ("media" + extension)
    payload = b"\x00media\xff\r\nexact bytes"
    path.write_bytes(payload)
    caption = "Aprovação — Gênesis 15–18"

    async def exercise() -> bytes:
        received: list[bytes] = []
        server, api_base = await _loopback_response(b'{"ok":true,"result":{"message_id":42}}', received)
        try:
            provider = TelegramNotificationProvider("fake-token", "123")
            provider.api_base = api_base
            assert await getattr(provider, "send_" + kind)("123", str(path), caption) == 42
            return received[0]
        finally:
            server.close()
            await server.wait_closed()

    request = asyncio.run(exercise())
    head, body = request.split(b"\r\n\r\n", 1)
    content_type = next(line for line in head.decode("iso-8859-1").split("\r\n") if line.lower().startswith("content-type:")).split(":", 1)[1].strip()
    message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + body)
    boundary = message.get_boundary().encode()
    assert body.startswith(b"--" + boundary + b"\r\n")
    assert body.endswith(b"\r\n--" + boundary + b"--\r\n")
    assert body.count(b"--" + boundary) == 4
    parts = list(message.iter_parts())
    assert [part.get_param("name", header="content-disposition") for part in parts] == ["chat_id", "caption", kind]
    assert parts[0].get_payload(decode=True) == b"123"
    assert parts[1].get_payload(decode=True) == caption.encode("utf-8")
    assert parts[2].get_payload(decode=True) == payload
    assert parts[2].get_content_type() == mime
    assert head.startswith(f"POST /botfake/send{kind.title()} HTTP/1.1".encode())


@pytest.mark.parametrize("response", [b"{}", b'{"ok":false,"result":{"message_id":42}}', b'{"ok":true,"result":{"message_id":0}}', b'{"ok":true,"result":{"message_id":true}}', b'{"ok":true,"result":{"message_id":"42"}}', b"not json"])
def test_media_invalid_response_fails_closed_over_loopback(tmp_path, response):
    path = tmp_path / "photo.png"
    path.write_bytes(b"image")

    async def exercise() -> None:
        received: list[bytes] = []
        server, api_base = await _loopback_response(response, received)
        try:
            provider = TelegramNotificationProvider("fake", "123")
            provider.api_base = api_base
            with pytest.raises((ValueError, UnicodeDecodeError)):
                await provider.send_photo("123", str(path), "caption")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["photo", "video"])
def test_large_media_never_falls_back_to_message(tmp_path, kind):
    path = tmp_path / "large.mp4"
    with path.open("wb") as file:
        file.truncate(50 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="50 MiB"):
        asyncio.run(getattr(TelegramNotificationProvider("fake", "123"), "send_" + kind)("123", str(path), "caption"))


def test_missing_destination_fails_before_transport(tmp_path, monkeypatch):
    path = tmp_path / "photo.png"
    path.write_bytes(b"image")
    monkeypatch.setattr(TelegramNotificationProvider, "_read_env", lambda *_args, **_kwargs: "")
    provider = TelegramNotificationProvider()
    with pytest.raises(ValueError, match="explicit Telegram destination"):
        asyncio.run(provider.send_photo("", str(path), "caption"))
    with pytest.raises(ValueError, match="explicit Telegram destination"):
        asyncio.run(provider.send_message("", "text"))
