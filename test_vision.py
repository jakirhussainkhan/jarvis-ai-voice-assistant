"""Tests for screen vision (vision.py) with the screen and model mocked."""

import desktop
import vision


# --------------------------------------------------------------------------- #
# a minimal stand-in for the OpenAI client
# --------------------------------------------------------------------------- #

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kwargs):
        self.outer.calls.append(kwargs)
        reply = (self.outer.replies.pop(0) if len(self.outer.replies) > 1
                 else self.outer.replies[0])
        return _Resp(reply)


class _Chat:
    def __init__(self, outer):
        self.completions = _Completions(outer)


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies) or [""]
        self.calls = []
        self.chat = _Chat(self)


def _png(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n not-a-real-png but exists")
    return path


# --------------------------------------------------------------------------- #
# ask_image
# --------------------------------------------------------------------------- #

def test_ask_image_needs_a_key(monkeypatch):
    monkeypatch.setattr(vision, "_client", None)
    assert "api key" in vision.ask_image("x.png", "hi").lower()


def test_ask_image_sends_a_base64_image(monkeypatch, tmp_path):
    client = FakeClient("A terminal window is open.")
    monkeypatch.setattr(vision, "_client", client)
    out = vision.ask_image(_png(tmp_path), "What is on screen?")
    assert out == "A terminal window is open."
    content = client.calls[0]["messages"][1]["content"]
    kinds = [part["type"] for part in content]
    assert kinds == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_ask_image_survives_api_error(monkeypatch, tmp_path):
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("model exploded")

    monkeypatch.setattr(vision, "_client", Boom)
    reply = vision.ask_image(_png(tmp_path), "hi").lower()
    assert "cannot access my vision" in reply
    assert "api key" in reply and "rate limits" in reply
    assert "model exploded" not in reply, "raw provider errors must stay internal"


# --------------------------------------------------------------------------- #
# action parsing / dispatch
# --------------------------------------------------------------------------- #

def test_parse_action_extracts_json_from_prose():
    assert vision._parse_action('Sure! {"action":"done","summary":"ok"}') == {
        "action": "done", "summary": "ok"}
    assert vision._parse_action("no json here") is None
    assert vision._parse_action("{not valid json}") is None


def test_do_action_dispatches_to_desktop(monkeypatch):
    seen = {}
    monkeypatch.setattr(desktop, "click_at",
                        lambda x, y, button=1, double=False: seen.update(
                            x=x, y=y, button=button, double=double) or "ok")
    monkeypatch.setattr(desktop, "type_text", lambda t: seen.update(text=t) or "ok")
    monkeypatch.setattr(desktop, "press_keys", lambda k: seen.update(keys=k) or "ok")

    vision._do_action({"action": "click", "x": 5, "y": 6})
    assert (seen["x"], seen["y"]) == (5, 6)
    vision._do_action({"action": "right_click", "x": 1, "y": 2})
    assert seen["button"] == 3
    vision._do_action({"action": "type", "text": "hello"})
    assert seen["text"] == "hello"
    vision._do_action({"action": "key", "keys": "ctrl+s"})
    assert seen["keys"] == "ctrl+s"


def test_do_action_unknown_is_reported():
    assert "don't know" in vision._do_action({"action": "explode"}).lower()


# --------------------------------------------------------------------------- #
# capture / look
# --------------------------------------------------------------------------- #

def test_capture_png_returns_none_when_capture_fails(monkeypatch):
    monkeypatch.setattr(desktop, "capture_screenshot", lambda path: None)
    assert vision.capture_png() is None


def test_look_cleans_up_the_screenshot(monkeypatch, tmp_path):
    shot = _png(tmp_path)
    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: shot)
    monkeypatch.setattr(vision, "ask_image", lambda p, q, **k: "a desktop")
    assert vision.look("what's on screen?") == "a desktop"
    assert not shot.exists()


def test_look_reports_capture_failure(monkeypatch):
    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: None)
    assert "couldn't capture" in vision.look().lower()


# --------------------------------------------------------------------------- #
# the perceive -> act loop
# --------------------------------------------------------------------------- #

def test_act_needs_a_key(monkeypatch):
    monkeypatch.setattr(vision, "_client", None)
    assert "api key" in vision.act("click x").lower()


def test_act_runs_actions_until_done(monkeypatch, tmp_path):
    shot = _png(tmp_path)
    monkeypatch.setattr(vision, "_client", object())
    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: shot)
    replies = ['{"action":"click","x":10,"y":20}',
               '{"action":"done","summary":"Clicked it."}']
    monkeypatch.setattr(vision, "ask_image", lambda p, q, **k: replies.pop(0))
    seen = []
    monkeypatch.setattr(desktop, "click_at",
                        lambda x, y, button=1, double=False: seen.append((x, y)) or "ok")

    assert vision.act("click the button") == "Clicked it."
    assert seen == [(10, 20)]


def test_act_stops_after_max_steps(monkeypatch, tmp_path):
    shot = _png(tmp_path)
    monkeypatch.setattr(vision, "_client", object())
    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: shot)
    monkeypatch.setattr(vision, "ask_image",
                        lambda p, q, **k: '{"action":"wait","seconds":0}')
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    assert "stopped after 2 steps" in vision.act("impossible", max_steps=2)


def test_act_reports_failure(monkeypatch, tmp_path):
    shot = _png(tmp_path)
    monkeypatch.setattr(vision, "_client", object())
    monkeypatch.setattr(vision, "capture_png", lambda max_width=None: shot)
    monkeypatch.setattr(vision, "ask_image",
                        lambda p, q, **k: '{"action":"fail","reason":"no such button"}')
    assert "no such button" in vision.act("click nothing")
