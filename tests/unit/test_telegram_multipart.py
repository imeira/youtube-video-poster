import asyncio
import io
import json
from email.parser import BytesParser
from email.policy import default

import pytest

from src.providers.notification.telegram_provider import TelegramNotificationProvider


@pytest.mark.parametrize('kind,extension,mime', [('photo', '.png', 'image/png'), ('video', '.mp4', 'video/mp4')])
def test_real_multipart_bytes_with_local_opener(tmp_path, monkeypatch, kind, extension, mime):
    path = tmp_path / ('media' + extension)
    payload = b'\x00media\xff\r\nexact bytes'
    path.write_bytes(payload)
    caption = 'Aprovação — Gênesis 15–18'
    calls = []

    def opener(request, timeout):
        calls.append(request)
        content_type = request.get_header('Content-type')
        message = BytesParser(policy=default).parsebytes(
            ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + request.data)
        boundary = message.get_boundary().encode()
        assert request.data.startswith(b'--' + boundary + b'\r\n')
        assert request.data.endswith(b'\r\n--' + boundary + b'--\r\n')
        assert request.data.count(b'--' + boundary) == 4
        parts = list(message.iter_parts())
        assert len(parts) == 3
        assert [p.get_param('name', header='content-disposition') for p in parts] == ['chat_id', 'caption', kind]
        assert parts[0].get_payload(decode=True) == b'123'
        assert parts[1].get_payload(decode=True) == caption.encode('utf-8')
        assert parts[2].get_payload(decode=True) == payload
        assert parts[2].get_content_type() == mime
        assert request.full_url.endswith('/send' + kind.title())
        return io.BytesIO(b'{"ok":true,"result":{"message_id":42}}')

    monkeypatch.setattr('urllib.request.urlopen', opener)
    provider = TelegramNotificationProvider('fake-token', '123')
    assert asyncio.run(getattr(provider, 'send_' + kind)('123', str(path), caption)) == 42
    assert len(calls) == 1


@pytest.mark.parametrize('response', [b'{}', b'{"ok":false,"result":{"message_id":42}}',
    b'{"ok":true,"result":{"message_id":0}}', b'{"ok":true,"result":{"message_id":true}}',
    b'{"ok":true,"result":{"message_id":"42"}}', b'not json'])
def test_media_invalid_response_fails_closed(tmp_path, monkeypatch, response):
    path = tmp_path / 'photo.png'
    path.write_bytes(b'image')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(response))
    with pytest.raises(ValueError):
        asyncio.run(TelegramNotificationProvider('fake', '123').send_photo('123', str(path), 'caption'))


@pytest.mark.parametrize('kind', ['photo', 'video'])
def test_large_media_never_falls_back_to_message(tmp_path, monkeypatch, kind):
    path = tmp_path / 'large.mp4'
    with path.open('wb') as f:
        f.truncate(50 * 1024 * 1024 + 1)
    def forbidden(*a, **k):
        pytest.fail('oversize media attempted HTTP')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    with pytest.raises(ValueError, match='50 MiB'):
        asyncio.run(getattr(TelegramNotificationProvider('fake', '123'), 'send_' + kind)('123', str(path), 'caption'))


def test_missing_destination_fails_before_http(tmp_path, monkeypatch):
    path = tmp_path / 'photo.png'
    path.write_bytes(b'image')
    monkeypatch.setattr(TelegramNotificationProvider, '_read_env', lambda *a, **k: '')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('HTTP attempted'))
    provider = TelegramNotificationProvider()
    with pytest.raises(ValueError, match='explicit Telegram destination'):
        asyncio.run(provider.send_photo('', str(path), 'caption'))
    with pytest.raises(ValueError, match='explicit Telegram destination'):
        asyncio.run(provider.send_message('', 'text'))
