import asyncio

from verbot.config import Settings
from verbot.speech import EspeakEngine, build_command


def test_build_command_uses_configured_voice():
    settings = Settings(espeak_voice="en-us", espeak_pitch=20, espeak_speed=150)
    cmd = build_command(settings, "hello")
    assert cmd == ["espeak-ng", "-v", "en-us", "-p", "20", "-s", "150", "--", "hello"]


def test_build_command_terminates_options_before_text():
    """Without `--`, text starting with a dash is parsed as a flag."""
    cmd = build_command(Settings(), "-v is not a flag here")
    assert cmd[-2] == "--"
    assert cmd[-1] == "-v is not a flag here"


async def test_say_runs_the_command(monkeypatch):
    calls: list[list[str]] = []

    class DummyProc:
        returncode = 0

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        return DummyProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    engine = EspeakEngine(Settings())
    await engine.say("hello robot")

    assert calls == [["espeak-ng", "-v", "en-gb", "-p", "10", "-s", "130", "--", "hello robot"]]


async def test_say_is_a_no_op_when_speech_disabled(monkeypatch):
    async def fail_exec(*args, **kwargs):
        raise AssertionError("should not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)

    engine = EspeakEngine(Settings(speech_enabled=False))
    await engine.say("silence")


async def test_say_serialises_overlapping_calls(monkeypatch):
    """Two concurrent says must not garble each other on one sound card."""
    running = 0
    max_concurrent = 0

    class DummyProc:
        returncode = 0

        async def wait(self):
            nonlocal running, max_concurrent
            running += 1
            max_concurrent = max(max_concurrent, running)
            await asyncio.sleep(0.01)
            running -= 1
            return 0

        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        return DummyProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    engine = EspeakEngine(Settings())
    await asyncio.gather(engine.say("one"), engine.say("two"))

    assert max_concurrent == 1


async def test_missing_espeak_is_reported_not_raised(monkeypatch):
    """A Pi without espeak-ng installed must not crash the API."""

    async def missing(*args, **kwargs):
        raise FileNotFoundError("espeak-ng")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)

    engine = EspeakEngine(Settings())
    await engine.say("hello")  # must not raise
