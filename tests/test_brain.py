from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from robot.brain.client import ChatClient
from robot.brain.prompt import parse_reply, system_prompt
from robot.brain.scripted import ScriptedBrain, extract_name


def test_parse_reply_variants():
    r = parse_reply('{"say": "Hi there!", "expression": "happy", "gesture": "nod", "remember": ["likes tea"]}')
    assert (r.say, r.expression, r.gesture, r.remember) == ("Hi there!", "happiness", "nod", ["likes tea"])
    r = parse_reply('Sure!\n```json\n{"say": "Yes.", "expression": "bogus", "gesture": "wave"}\n```')
    assert (r.say, r.expression, r.gesture) == ("Yes.", "neutral", None)
    r = parse_reply("<think>hmm</think>Just plain text reply.")
    assert r.say == "Just plain text reply." and r.expression == "neutral"
    r = parse_reply('prefix {"say": "embedded"} suffix')
    assert r.say == "embedded"
    r = parse_reply('{"say": "", "remember": "a string"}')
    assert r.say == "Hmm." and r.remember == ["a string"]
    assert parse_reply("").say == "Hmm."


def test_system_prompt_mentions_person_and_facts():
    p = system_prompt("Persona.", "Ann", ["likes tea"])
    assert "Ann" in p and "likes tea" in p and '"say"' in p
    assert "do not recognise" in system_prompt("P", None, [])


def test_scripted_brain_rules():
    b = ScriptedBrain("Buddy")
    assert b.reply("hello", "Ann", []).say.startswith("Hello, Ann")
    r = b.reply("Please remember that I like tea", "Ann", [])
    assert r.remember == ["i like tea"] and r.gesture == "nod"
    assert "Buddy" in b.reply("what is your name?", None, []).say
    assert "don't know your name" in b.reply("who am I", None, []).say
    assert "Ann" in b.reply("who am I?", "Ann", []).say
    assert "likes tea" in b.reply("what do you know about me", "Ann", ["likes tea"]).say
    assert b.reply("tell me a joke", None, []).expression == "excitement"
    assert b.reply("", None, []).expression == "confusion"
    assert b.reply("is it raining?", None, []).expression == "thinking"
    assert extract_name("my name is Ann") == "Ann"
    assert extract_name("I'm fine thanks") is None
    assert extract_name("call me Bob") == "Bob"


class _Handler(BaseHTTPRequestHandler):
    reject_json_mode = False

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "tiny"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        if _Handler.reject_json_mode and "response_format" in req:
            self.send_response(400)
            self.end_headers()
            return
        user = req["messages"][-1]["content"]
        content = json.dumps({"say": f"echo: {user}", "expression": "happiness", "gesture": "nod", "remember": []})
        body = json.dumps(
            {
                "model": "tiny",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_llm():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_chat_client_against_fake_server(fake_llm):
    c = ChatClient(fake_llm, "tiny")
    assert c.healthy() and c.models() == ["tiny"]
    res = c.chat([{"role": "user", "content": "hi"}])
    assert "echo: hi" in res.text and res.prompt_tokens == 10
    _Handler.reject_json_mode = True
    try:
        res2 = c.chat([{"role": "user", "content": "again"}])
        assert "echo: again" in res2.text and c._json_mode_ok is False
    finally:
        _Handler.reject_json_mode = False
    c.close()
    assert ChatClient("http://127.0.0.1:1/v1").healthy() is False
