import threading
import time
from pathlib import Path

import pytest

import surf


def test_project_script_points_to_run_cli():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'surf = "surf:run_cli"' in pyproject.read_text(encoding="utf-8")


def test_run_cli_exits_with_130_on_keyboard_interrupt(monkeypatch):
    def _raise_keyboard_interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(surf, "main", _raise_keyboard_interrupt)

    with pytest.raises(SystemExit) as exc_info:
        surf.run_cli()

    assert exc_info.value.code == 130


def test_call_interruptibly_raises_keyboard_interrupt_when_interrupted():
    surf._INTERRUPTED = False

    def _block():
        time.sleep(5)

    interrupter = threading.Thread(
        target=lambda: (time.sleep(0.05), setattr(surf, "_INTERRUPTED", True)),
        daemon=True,
    )
    interrupter.start()

    try:
        with pytest.raises(KeyboardInterrupt):
            surf._call_interruptibly(_block, poll_interval=0.01)
    finally:
        surf._INTERRUPTED = False
