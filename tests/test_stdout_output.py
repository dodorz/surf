import surf


class _FakeConfig:
    def get_path(self, section, key, fallback=None):
        return fallback or "."


class _FakeStdout:
    def __init__(self):
        self.reconfigured = False
        self.writes = []
        self.flushed = False

    def reconfigure(self, **kwargs):
        self.reconfigured = True

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        self.flushed = True


def test_save_html_to_stdout_reconfigures_utf8(monkeypatch):
    fake_stdout = _FakeStdout()
    monkeypatch.setattr(surf.sys, "stdout", fake_stdout)

    surf.OutputHandler.save_html(
        "Example",
        "<p>Copyright © Example</p>",
        _FakeConfig(),
        output_path="-",
    )

    assert fake_stdout.reconfigured is True
    assert any("©" in chunk for chunk in fake_stdout.writes)
    assert fake_stdout.flushed is True
