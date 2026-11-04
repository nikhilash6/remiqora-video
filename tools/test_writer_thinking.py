"""The writer's thinking, live (app/llm/client.py: Thoughts, _post_stream, _Split) without a GPU: a fake llama-server
streams a reply; the thinking lands in job.progress["thinking"] a few times (throttled), the answer and the cache are
the same as the plain call's, a cached call shows the thinking saved beside it, a <think> block in the content is
split off, the retry of a cut answer still works.

    .venv\\Scripts\\python.exe tools\\test_writer_thinking.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.llm import client  # noqa: E402

failed = 0


def ok(cond, what: str) -> None:
    global failed
    if cond:
        print(f"  ok  {what}")
    else:
        failed += 1
        print(f"FAIL  {what}")


ANSWER = {"title_ru": "Кофе на рассвете", "scenes": [{"en": "A woman pours coffee at dawn.", "ru": "Женщина наливает кофе на рассвете."}]}
SCHEMA = {"type": "object", "properties": {"title_ru": {"type": "string"},
                                           "scenes": {"type": "array", "items": {"type": "object", "properties": {
                                               "en": {"type": "string"}, "ru": {"type": "string"}}, "required": ["en", "ru"]}}},
          "required": ["title_ru", "scenes"], "additionalProperties": False}
REASONING = "Думаю о рассвете. " * 40   # 40 pieces, streamed one by one

# what the fake server answers: mode, and the requests it got
S = {"mode": "reasoning", "bodies": [], "replies": []}


def _chunks(s: str, n: int) -> list[str]:
    return [s[i:i + n] for i in range(0, len(s), n)]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        S["bodies"].append(body)
        mode = S["replies"].pop(0) if S["replies"] else S["mode"]
        answer = json.dumps(ANSWER, ensure_ascii=False)
        if mode == "broken":
            answer = answer[:25]
        if not body.get("stream"):
            msg = {"content": answer, "reasoning_content": REASONING}
            out = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(delta: dict, finish=None) -> None:
            ev = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        if mode == "inline":   # a server that leaves <think> in the content, cut at odd places
            for piece in _chunks("<think>" + REASONING + "</think>" + answer, 7):
                send({"content": piece})
                time.sleep(0.005)
        else:
            for piece in REASONING.split(". ")[:-1]:
                send({"reasoning_content": piece + ". "})
                time.sleep(0.03)
            for piece in _chunks(answer, 9):
                send({"content": piece})
        send({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class Progress(dict):
    """A job's progress that counts how often the thinking was written and keeps a live snapshot."""

    def __init__(self):
        super().__init__()
        self.writes = 0
        self.live: list[dict] = []

    def __setitem__(self, k, v):
        if k == "thinking":
            self.writes += 1
            if v.get("live") and v.get("text"):
                self.live.append(v)
        super().__setitem__(k, v)


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client.URL = f"http://127.0.0.1:{srv.server_address[1]}"
    client.cloud = lambda: False
    tmp = Path(tempfile.mkdtemp(prefix="thinking-"))
    gguf = tmp / "fake.gguf"
    gguf.write_bytes(b"x" * 10)
    m = client.LlmModel(id="t/fake", name="fake", path=gguf, size_gb=0, mmproj=None, source="test")

    async def call(cache_dir: Path, progress=None, step="script", **kw):
        config.LLM_CACHE = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        client.think_into(progress)
        c = client.Client(m)
        d = await c.chat_json("You write.", "Coffee at dawn.", SCHEMA, temperature=0.8, seed=5, max_tokens=900, step=step, **kw)
        return c, d

    print("plain vs streamed")
    a, b = tmp / "a", tmp / "b"
    _, plain = asyncio.run(call(a))
    ok(S["bodies"][-1].get("stream") is None, "no job: the plain call, no stream")
    prog = Progress()
    t0 = time.time()
    c, streamed = asyncio.run(call(b, prog))
    took = time.time() - t0
    ok(S["bodies"][-1].get("stream") is True, "in a job: stream: true")
    ok({k: v for k, v in S["bodies"][-1].items() if k != "stream"} == S["bodies"][-2], "the request is otherwise the same")
    ok(streamed == plain == ANSWER, "the same answer")
    fa, fb = list(a.glob("*.json")), list(b.glob("*.json"))
    ok(len(fa) == len(fb) == 1 and fa[0].name == fb[0].name, "the same cache key")
    ok(fa[0].read_text(encoding="utf-8") == fb[0].read_text(encoding="utf-8"), "the same cached answer")
    ok(not list(a.glob("*.think.txt")), "no thinking saved without a job")
    tf = list(b.glob("*.think.txt"))
    ok(len(tf) == 1 and tf[0].read_text(encoding="utf-8") == REASONING.strip(), "the thinking saved beside the answer")
    th = prog.get("thinking") or {}
    ok(th.get("live") is False and th.get("step") == "script" and th.get("calls") == 1 and th.get("cached") == 0, "done: live off, step, one call")
    ok(th.get("text") == REASONING, "the whole thinking in the progress")
    ok(0 < th.get("secs", 0) <= took + 0.1, f"thinking time {th.get('secs')} s of {took:.2f} s")
    ok(len(prog.live) >= 2, f"live updates while it thinks ({len(prog.live)})")
    ok(prog.writes <= 2 + took / client.THINK_EVERY + 1, f"throttled: {prog.writes} writes for 40 pieces in {took:.2f} s")
    ok(all(len(x["text"]) <= client.THINK_TAIL for x in prog.live), "the live text is a tail")
    grow = [len(x["text"]) for x in prog.live]
    ok(grow == sorted(grow) and grow[0] < len(REASONING), "the live text grows")

    print("cached")
    n = len(S["bodies"])
    prog2 = Progress()
    c2, again = asyncio.run(call(b, prog2, step="places"))
    ok(len(S["bodies"]) == n and c2.cached == 1, "answered from the cache, no request")
    ok(again == ANSWER, "the cached answer")
    th2 = prog2.get("thinking") or {}
    ok(th2.get("cached") == 1 and th2.get("calls") == 0 and th2.get("live") is False, "cached: counted, not live")
    ok(th2.get("text") == REASONING.strip() and th2.get("secs") == 0 and th2.get("step") == "places", "the saved thinking shown, no time")
    prog3 = Progress()
    _, plain_cached = asyncio.run(call(a, prog3))
    ok(plain_cached == ANSWER and (prog3.get("thinking") or {}).get("text") == "", "a cache entry without saved thinking: empty text")

    print("several calls in one job")
    prog4 = Progress()
    asyncio.run(call(tmp / "c", prog4, step="look"))
    first = dict(prog4["thinking"])
    asyncio.run(call(tmp / "c", prog4, step="script", use_cache=False))
    th4 = prog4["thinking"]
    ok(th4["calls"] == 2 and th4["step"] == "script", "two calls, the last step")
    ok(th4["secs"] >= first["secs"], "the time adds up")
    ok(len(th4["text"]) <= client.THINK_TAIL, "the text stays a tail across calls")
    parts = th4.get("parts") or []
    ok([x["step"] for x in parts] == ["look", "script"] and not any(x["live"] for x in parts), "parts: look, then script, done")
    ok(th4["text"][parts[1]["at"]:] == REASONING and th4["text"][:parts[1]["at"]].strip() == REASONING.strip(),
       "each part's text starts at its `at`")
    ok(abs(sum(x["secs"] for x in parts) - th4["secs"]) <= 0.15, "the parts' seconds add up to the total")

    print("the step label does not lag")
    prog7 = Progress()
    asyncio.run(call(tmp / "g", prog7, step="look", use_cache=False))
    seen7: list[dict] = []

    class Spy(dict):
        def __setitem__(self, k, v):
            if k == "thinking":
                seen7.append(v)
            super().__setitem__(k, v)
    spy = Spy(prog7)
    asyncio.run(call(tmp / "g", spy, step="script", use_cache=False))
    first7 = seen7[0]
    ok(first7["live"] and first7["step"] == "script" and first7["parts"][-1]["step"] == "script"
       and first7["text"][first7["parts"][-1]["at"]:] == "", "a new step starts with its own empty part, not the look's text")
    ok(first7["parts"][0]["step"] == "look" and first7["parts"][0]["live"] is False, "the look's part is done")
    prog8 = Progress()
    for _ in range(3):
        asyncio.run(call(tmp / "h", prog8, step="lines", use_cache=False))
    p8 = prog8["thinking"]["parts"]
    ok(len(p8) == 1 and p8[0]["calls"] == 3 and p8[0]["step"] == "lines", "the same step in a row is one part")
    ok(len(prog8["thinking"]["text"]) <= client.THINK_TAIL and p8[0]["at"] == 0, "a part cut by the tail starts at 0")

    print("<think> in the content")
    S["mode"] = "inline"
    prog5 = Progress()
    _, d5 = asyncio.run(call(tmp / "d", prog5))
    ok(d5 == ANSWER, "the answer without the <think> block")
    ok(prog5["thinking"]["text"] == REASONING, "the block is the thinking")
    sp = client._Split()
    ok(sp.content("{\"a\"") == ("", "{\"a\"") and sp.finish() == "{\"a\"", "a plain answer passes through")
    sp = client._Split()
    sp.content("<thi")
    sp.content("nk>x</th")
    sp.content("ink>{}")
    ok(sp.finish() == "{}" and "".join(sp.thought) == "x", "tags cut across pieces")
    S["mode"] = "reasoning"

    print("retries")
    S["replies"] = ["broken"]
    prog6 = Progress()
    c6, d6 = asyncio.run(call(tmp / "e", prog6))
    ok(d6 == ANSWER and c6.retries == 1 and c6.calls == 2, "a cut answer asked once more")
    ok(S["bodies"][-1]["seed"] == 5 + 7919 and S["bodies"][-1]["stream"] is True, "the retry: another seed, streamed")
    ok(prog6["thinking"]["calls"] == 2 and prog6["thinking"]["live"] is False, "both calls in the thinking")
    S["replies"] = ["broken", "broken"]
    try:
        asyncio.run(call(tmp / "f", Progress()))
        ok(False, "twice cut: an error")
    except client.LLMError as e:
        ok("дважды" in str(e) or "twice" in str(e), "twice cut: «дважды оборвала ответ»")
    ok(not list((tmp / "f").glob("*")), "twice cut: nothing cached")

    print("the job's context")
    from app import jobs
    seen = {}

    async def task(job):
        seen["think"] = client.THINK.get() is job.progress
        return 1

    async def run_one():
        client.think_into(None)
        job = jobs.submit_task("write", {}, task)
        w = asyncio.ensure_future(jobs.worker())
        for _ in range(100):
            if job.status == "done":
                break
            await asyncio.sleep(0.01)
        w.cancel()
        return job

    j = asyncio.run(run_one())
    ok(j.status == "done" and seen.get("think"), "a task job's calls write into its progress")
    srv.shutdown()


if __name__ == "__main__":
    main()
    print("\nall ok" if not failed else f"\n{failed} FAILED")
    sys.exit(1 if failed else 0)
