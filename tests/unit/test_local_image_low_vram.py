"""Low-VRAM IP-Adapter setup order regression tests."""

from types import SimpleNamespace

from src.providers.image.local_sd15_provider import LocalSD15Provider


class FakeVAE:
    def __init__(self, calls):
        self.calls = calls
        self.decode = lambda *args, **kwargs: (args, kwargs)

    def to(self, target):
        self.calls.append(("vae.to", target))
        return self


class FakePipeline:
    def __init__(self):
        self.calls = []
        self.vae = FakeVAE(self.calls)

    def load_ip_adapter(self, *args, **kwargs):
        self.calls.append(("load_ip_adapter", args, kwargs))

    def enable_attention_slicing(self, size):
        self.calls.append(("enable_attention_slicing", size))


def test_ip_adapter_does_not_replace_processors_with_attention_slicing():
    pipe = FakePipeline()
    torch_stub = SimpleNamespace(float32="float32")

    LocalSD15Provider._configure_ip_adapter(pipe, torch_stub)

    names = [call[0] for call in pipe.calls]
    assert "load_ip_adapter" in names
    assert "enable_attention_slicing" not in names
