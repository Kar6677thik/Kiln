# K5 — Async HTTP Gateway, Backpressure, and an Event-Loop Watchdog

> **Status:** 📝 specified · **Depends on:** K1 (master, KP/1, framing), K2 (spawn launcher, used to start the gateway), K4 (asyncio primers, `async-await-basics`) · **Branch:** `milestone/k5-gateway` · **Size:** L (8 steps)
> **Incident:** research §2.6 (sync code such as boto3, `requests`, ORMs or big JSON parses inside `async def` blocked the event loop. Moving it to `asyncio.to_thread()` gave about 31–36% more throughput and about 24–27% lower p99)
> **Reference implementations (read *after* building):** [uvicorn](https://github.com/encode/uvicorn) + [h11](https://github.com/python-hyper/h11) (HTTP), [aiodebug](https://pypi.org/project/aiodebug/) and [loopmon](https://pypi.org/project/loopmon) (loop-lag watchdogs), asyncio's own debug mode

---

## 1. The story (plain English)

An **event loop** is a single thread that juggles thousands of connections. It works like a waiter running between tables. It never stands at one table waiting for a diner to decide; it takes the next order that's ready. In Python that's `asyncio`. Every `await` is the waiter saying "I'll come back when this is ready". That frees the loop to serve someone else.

The trap is that **one call that doesn't `await` freezes the entire loop.** If a handler calls `requests.get(...)`, `time.sleep(1)`, or `json.loads` on a 50 MB document, the waiter is stuck at that one table, and *every* other connection waits. Latency goes up for everybody, health checks time out, and load balancers decide the server is dead. Research §2.6 describes a PDF-ingestion service where exactly this happened. Moving the blocking calls into a thread with `asyncio.to_thread()` gave roughly a third more throughput and a quarter lower tail latency.

What makes this bug nasty is that **nothing crashes**. The server only gets slow, so you need a tool that *notices* the loop is stuck and tells you *which line* is stuck. That tool is a **watchdog**: a separate thread that pings the loop every few milliseconds and shouts, with a stack trace, when the loop stops answering.

**In this milestone we will** build Kiln's HTTP front door from scratch on `asyncio`: our own HTTP/1.1 parser, a connection pool to the master, and **backpressure**, which means saying "429, slow down" instead of queueing forever. Then we plant blocking calls, watch the watchdog catch them, inspect the live process with `python -m asyncio ps`, and measure the before/after improvement of moving the work off the loop.

## 2. What you will learn

- **Explain** how an event loop works on top of `selectors` (link to K1's master loop), and what a coroutine, a Task and a Future are. *(asyncio)*
- **Explain** why a blocking call stalls every connection, and **predict** the latency effect of a stall of N ms at a given request rate. *(asyncio)*
- **Explain** cancellation, `TaskGroup` and `asyncio.timeout`, and why cancellation is *cooperative*. *(asyncio)*
- **Explain** HTTP/1.1 message framing (`Content-Length`, keep-alive, pipelining) and why accepting both `Content-Length` and `Transfer-Encoding` is a security problem (request smuggling). *(Packaging & security, Concurrency)*
- **Explain** backpressure: why unbounded queues turn overload into outage, and how bounded concurrency + 429 + `Retry-After` + `writer.drain()` prevent it. *(Concurrency)*
- **Explain** how a watchdog thread can read another thread's stack (`sys._current_frames()`), and why it *can't* do that while the loop thread holds the GIL inside C code. *(Runtime internals, Concurrency)*
- **Use** `python -m asyncio ps` and `pstree` (3.14+) to inspect a live process's tasks. *(Testing & ops)*

## 3. Entry criteria

- K1: a master with KP/1 on `127.0.0.1:7340`, `FrameDecoder` in `protocol/framing.py`, the supervisor's spawn/reap/backoff logic.
- K2: the `spawn` launcher (`launch/spawn.py`) able to start an arbitrary `python -m <module>` child with config passed as JSON.
- K4: the primers `python/async-await-basics.md` and `python/asyncio-run.md` exist. K5 builds on them.
- `kiln doctor` reports ptrace status (needed for gate G5.6).

## 4. Scope

### In scope
- `src/kiln/gateway/` modules: `__main__.py`, `server.py`, `http11.py`, `routes.py`, `kpclient.py`, `backpressure.py`, `watchdog.py`, `metrics.py` (spec/01 §5).
- Gateway supervision by the master: start, restart with backoff, and shutdown.
- All routes in spec/02 §5.2, the KP→HTTP error mapping, `/metrics` in Prometheus text format, `/debug/loop` and `/debug/block`.
- One additional lab-only route: `POST /debug/blocking-submit` (§5.7).
- A lab HTTP load generator `labs/k5/http_load.py`.
- The watchdog, plus a comparison with asyncio debug mode and `faulthandler.dump_traceback_later`.

### Out of scope (and why)
- TLS, HTTP/2, chunked transfer encoding and `Expect: 100-continue` handling. They're excluded by spec/02 §5.1, because each is a separate topic.
- Authentication (ADR-021).
- Restarting the gateway on `SIGHUP`. The gateway runs no user code, so a worker reload doesn't need to touch it. A gateway config change needs a full restart. This is documented in `kiln serve --help`.
- Running HTTP inside the master (ADR-006 / ADR-014).

## 5. Design

### 5.1 The gateway process and its supervision (extends spec/04)

- **Start:** when `gateway.enabled = true`, the master starts the gateway **after its own KP listener opens** (that is, after the first worker READY, per ADR-013), using the spawn launcher: `python -m kiln.gateway --config-json '<json>'`. It does **not** use `start.method`. The gateway is always spawned, because it must never inherit the app or the master's fds (ADR-014).
- **Handle:** the master keeps a `GatewayHandle` (pid, state `BOOTING|RUNNING|STOPPING|EXITED`, `launched_mono`, `kill_sent`, consecutive failures). It is **not** in the worker table, doesn't count toward `workers`, and never gets jobs.
- **Readiness:** the gateway writes the single line `KILN_GATEWAY_READY <port>\n` to its **stdout** once it's listening. The master passes a pipe as the child's stdout, registers that pipe in its selector, and moves the gateway to RUNNING when it reads the line. Everything after that line on stdout is logged at INFO with `role=gateway` (the gateway's own logging goes to stderr per spec/06). If the line doesn't arrive within `master.boot_timeout_s`, the master kills it (SIGKILL) and backs off.
- **Death:** reaped by the same `waitpid` loop. Classification uses spec/04 §4.1 with role `gateway`. The restart backoff is the same formula as spec/04 §5.1. **After 5 consecutive failures the master keeps running without a gateway** and logs CRITICAL `event=gateway_gave_up`. spec/06 §2.2 has no gateway-supervision events yet, so step 4 adds `gateway_started` (INFO: `pid`, `bind`), `gateway_died` (ERROR: same fields as `worker_died`) and `gateway_gave_up` (CRITICAL: `failures`) there. The KP path still works, so job service continues. This is deliberately softer than the worker crash-loop rule, because the gateway isn't essential to job execution.
- **Shutdown:** in graceful master shutdown, step 1 (closing the listener) also sends **SIGTERM** to the gateway. The gateway then:
  1. stops accepting
  2. answers in-flight `wait` long-polls immediately with `202` + the current JobView
  3. finishes the other in-flight requests
  4. closes idle keep-alive connections
  5. exits 0 within `master.graceful_timeout_s`, or gets SIGKILLed.
- **Spec note:** spec/04 has no gateway section. Step 4 adds `§8 Gateway supervision` to spec/04, mirroring this subsection.

### 5.2 `http11.py`: a sans-IO HTTP/1.1 parser

**Sans-IO** means the parser never touches sockets. You feed it bytes and it hands back events, exactly like `FrameDecoder` in K1 and like h11. That makes it testable without a network.

```python
class HttpParseError(Exception):
    status: int        # 400, 413, 431, 501, 505 ...
    reason: str        # human message, ends up in the JSON error body

@dataclass(frozen=True, slots=True)
class Request:
    method: str                     # "GET" | "POST" | "DELETE" (others → 405 at routing, not parse error)
    target: str                     # raw request-target, e.g. "/v1/jobs/abc/wait?timeout_s=5"
    path: str                       # percent-decoded path
    query: dict[str, list[str]]     # urllib.parse.parse_qs(keep_blank_values=True)
    version: tuple[int, int]        # (1, 1) or (1, 0)
    headers: list[tuple[str, str]]  # names lowercased, order preserved
    body: bytes
    keep_alive: bool                # computed per §5.2 rules

def parse_head(block: bytes) -> tuple[str, str, tuple[int,int], list[tuple[str,str]]]: ...
def body_length(headers) -> int: ...          # raises HttpParseError
def keep_alive(version, headers) -> bool: ...
def render_response(status: int, body: bytes, *, content_type: str, keep_alive: bool,
                    extra_headers: list[tuple[str,str]] = ()) -> bytes: ...
```

**Parsing rules** (each one has a test):

| Rule | Behaviour |
|---|---|
| Line endings | the header block ends at `\r\n\r\n`. Lines are split on `\r\n`. A bare `\n` → 400 `"bare LF not allowed"` |
| Request line | exactly `METHOD SP target SP HTTP/x.y`. The method is uppercase token chars. A version other than 1.0/1.1 → 505 |
| Header line | `name ":" OWS value OWS`. The name is token chars only (whitespace before the colon → 400). Obsolete line folding (a line starting with SP/HTAB) → 400 |
| Header block size | > 16 KiB → 431. Enforced by the stream limit in §5.3 |
| `Transfer-Encoding` present | → 501 `"chunked not supported"`. **If `Content-Length` is also present → 400**, because that's the request-smuggling defence. The primer explains why |
| `Content-Length` | it must be ASCII digits. Several headers with *different* values → 400. Equal duplicates are allowed. > `gateway.max_body_bytes` → 413 |
| No `Content-Length` on POST | body length 0 (and a POST to `/v1/jobs` then fails at the JSON stage with 400) |
| Keep-alive | HTTP/1.1: keep-alive unless `Connection: close`. HTTP/1.0: close unless `Connection: keep-alive` |
| `Expect: 100-continue` | ignored. The body is read normally (spec/02 §5.1) |

`render_response` always sets `Content-Length`, `Content-Type`, `Connection: keep-alive|close` and `Server: kiln/<version>`, plus any extra headers (for example `Retry-After`, `Location`).

### 5.3 `server.py`: connections on asyncio streams

- `asyncio.start_server(handle_conn, host, port, limit=16 * 1024, backlog=1024)`. **`limit`** caps how much `StreamReader.readuntil` will buffer. Overflowing it raises `asyncio.LimitOverrunError` → 431 and close. That's the "no unbounded buffers" rule for headers.
- **Per connection, strictly sequential:**
  ```
  loop:
      async with asyncio.timeout(first_byte_timeout):     # keepalive_timeout_s for 2nd+ request; header_timeout_s for 1st
          head = await reader.readuntil(b"\r\n\r\n")      # 431 on LimitOverrunError
      parse → Request (body via await reader.readexactly(n) under header_timeout_s)
      response = await routes.dispatch(request)           # see §5.4–5.6
      writer.write(response_bytes); await writer.drain()  # drain = backpressure toward slow readers
      if not keep_alive: break
  ```
  Handling one request at a time per connection makes **pipelined requests answer strictly in order** (spec/02 §5.1) with no extra machinery.
- **Idle timeout:** the keep-alive idle timeout closes the connection silently, with no response.
- **The connection's task** is created by `start_server`. All per-connection tasks are tracked in a `set`, so shutdown can wait for them (`asyncio.wait(..., timeout=graceful)`).
- **Error safety:**
  - every exception inside `dispatch` → 500 with code `INTERNAL`, and the traceback logged (spec/06 `event=http_internal_error`)
  - `ConnectionResetError` or `IncompleteReadError` from the client → close quietly, logged at DEBUG

### 5.4 `kpclient.py`: a pipelined connection pool to the master

```python
class KPConnection:
    async def connect(self) -> None: ...
    async def request(self, msg: dict, *, timeout_s: float) -> dict: ...   # returns the `ok` or `error` message
    pending: int                                                           # outstanding requests

class KPPool:
    def __init__(self, host: str, port: int, size: int): ...               # size = gateway.master_connections
    async def request(self, msg: dict, *, timeout_s: float) -> dict: ...   # picks least-pending connection
```

- **Pipelining:**
  1. Each connection keeps `next_req_id` (an int, starting at 1) and `waiters: dict[int, asyncio.Future]`.
  2. `request` assigns `req_id`, stores a Future, writes the frame (encoded with `protocol/framing.py`), `await drain()`s, and then awaits the Future under `asyncio.timeout(timeout_s)`.
  3. A single **reader task** per connection loops on `reader.read(65536)` and feeds the `FrameDecoder`. For each message it pops `waiters[req_id]` and calls `set_result`. Responses may arrive out of order (spec/02 §3), which this handles naturally.
- **Timeouts:**
  - a KP request timeout → HTTP 504 `{"error":{"code":"MASTER_TIMEOUT",...}}`
  - the default timeout is 5 s, or `timeout_s + 5` for `wait`
  - the Future is removed from `waiters`, and a late reply is dropped and logged at DEBUG
- **Master connection lost** (EOF or a reset):
  1. fail every pending Future with `ConnectionError`, which maps to HTTP 503 `MASTER_UNAVAILABLE`
  2. mark the connection down
  3. reconnect with backoff of 0.1 s doubling to 5 s
  4. `/healthz` reports 503 while all connections are down
- **Boundedness argument** (this goes in the primer): each HTTP request in flight has **at most one** KP request outstanding, and HTTP requests in flight are capped by `max_inflight`, so KP pending is ≤ `max_inflight` with no extra limit needed.
- **Two new HTTP error codes, `MASTER_TIMEOUT` (504) and `MASTER_UNAVAILABLE` (503).** These are gateway-only, so they don't belong to spec/02 §3.3's KP codes. Step 3 adds them to spec/02 §5.2's error mapping paragraph.

### 5.5 `backpressure.py`: say no early

```python
class InflightLimiter:
    def __init__(self, limit: int): ...
    def try_acquire(self) -> bool: ...     # non-blocking: False if limit reached
    def release(self) -> None: ...
    @property
    def inflight(self) -> int: ...
```

- It's a plain counter. That's enough, because all access happens on the loop thread, which makes it single-threaded by construction. **Why not `asyncio.Semaphore`?** We never want to *wait* for a slot. Waiting is just an unbounded queue in disguise. Over the limit we answer **immediately**: `429` + `Retry-After: 1` + `{"error":{"code":"GATEWAY_OVERLOADED",...}}` (a gateway-only code like `MASTER_TIMEOUT`, which step 3 adds to spec/02 §5.2). The primer must make this point explicitly ("a semaphore you await is a queue").
- **What it covers:** every route except `/healthz` and `/metrics` acquires a slot. Those two are exempt so operators can still see an overloaded server. `wait` long-polls **do** hold a slot for their whole duration. This trade-off is documented, and the lab measures it (§7 run C).
- **The master's `QUEUE_FULL`** → `429` with `Retry-After: <ceil(retry_after_s)>`.
- **Writes:** `await writer.drain()` after every response means a client that reads slowly slows only its *own* connection task and doesn't pile bytes into memory.
- **Metrics** (spec/06 §4.1 names): `kiln_http_inflight` gauge and `kiln_http_overloaded_total` counter (gateway 429s only). Master `QUEUE_FULL` rejections show up in `kiln_jobs_rejected_total`. The `overloaded` log event is rate-limited per spec/06 §2.8.

### 5.6 `routes.py`

A plain dispatch table `[(method, compiled_regex, handler)]`, matched in order. 404 for an unknown path, and 405 (with an `Allow` header) when the path matches but the method doesn't. Handlers implement spec/02 §5.2 exactly:

- `POST /v1/jobs`: body JSON object → KP `submit`. On `ok` → `202` + `Location: /v1/jobs/<id>`.
- `GET /v1/jobs/<id>/wait?timeout_s=N`: N is parsed as a float with 0 < N ≤ 60 (else 400). KP `wait` with `timeout_s=N`. If `timed_out` → 202.
- `/debug/*`: all of these return 404 unless `debug.enabled`. A disabled debug feature has to be indistinguishable from a route that doesn't exist (ADR-021).

### 5.7 Lab-only route `POST /debug/blocking-submit`

It reproduces research §2.6 on purpose. It's only registered when `debug.enabled = true` **and** `labs.enabled = true`.

- Query: `mode=block|thread` and `work=sleep|json`, plus `work_ms=N` (for `sleep`) or `doc=small|large` (for `json`).
- Behaviour: do some "enrichment" work, then submit `labs.echo` as normal.
  - `work=sleep`: `time.sleep(N/1000)`, standing in for a sync HTTP call like boto3 or `requests`. Under `mode=thread` it's `await asyncio.to_thread(time.sleep, N/1000)`.
  - `work=json`: `json.loads(DOC)`, where `DOC` is generated once at gateway start. `large` is sized so one parse takes on the order of tens of ms on this machine; the exact size is calibrated in step 8 and recorded. Under `mode=thread` it's `await asyncio.to_thread(json.loads, DOC)`.
- **Spec note:** this adds a route to spec/02 §5.2 (step 7 updates the table).

### 5.8 `watchdog.py`: the loop-lag detector

```python
class LoopWatchdog:
    def __init__(self, loop, *, interval_ms: int, threshold_ms: int, log, ring_size: int = 10): ...
    def start(self) -> None: ...          # starts daemon thread "kiln-watchdog"
    def stop(self) -> None: ...
    def snapshot(self) -> dict: ...       # for /debug/loop and /metrics
```

**Algorithm** (the watchdog thread):

```
seq = 0
loop forever:
    seq += 1; sent = perf_counter()
    loop.call_soon_threadsafe(self._beat, seq, sent)    # the loop thread will run _beat when it is free
    wait up to interval_ms for _beat(seq) to have run (threading.Event)
    while _beat(seq) has not run:
        lag = perf_counter() - sent
        if lag ≥ threshold_ms and this stall not yet reported:
            frame = sys._current_frames().get(loop_thread_ident)
            stack = traceback.format_stack(frame) (or "<unavailable>")
            log WARNING event=loop_stall lag_ms=… threshold_ms=… stack=… task=…   # once per stall (spec/06 §2.8)
            remember stall(start=sent, stack)
        wait a further interval_ms (Event.wait)
    if a stall was reported: log INFO event=loop_stall_recovered stall_ms=…, push to ring buffer
```

- `_beat(seq, sent)` runs **on the loop thread**. It computes `lag = perf_counter() - sent`, adds it to the **lag histogram**, which uses spec/06 §4.1's loop-lag buckets (`0.001 … 5, +Inf` seconds), and sets the Event.
- `loop_thread_ident` is captured with `threading.get_ident()` from inside the loop at startup.
- `/debug/loop` returns `{interval_ms, threshold_ms, beats, histogram, stalls_total, max_lag_ms, recent_stalls: [{started_wall_ms, duration_ms, stack}]}`.
- `/metrics` exposes `kiln_loop_lag_seconds` (histogram) and `kiln_loop_stalls_total` (counter), using the spec/06 §4.1 names. The `task` field of `loop_stall` is the name of the asyncio task the loop thread was running, obtained with `asyncio.current_task(loop)` read **from the watchdog thread**. That read is racy but harmless for a diagnostic: it may say `unknown`.
- **The GIL caveat** (the lab must demonstrate it, and the primer must explain it):
  - The watchdog thread needs the GIL to run Python code.
  - During `time.sleep`, the loop thread *releases* the GIL, so the watchdog fires mid-stall and captures the stack pointing at the guilty handler line.
  - During a long `json.loads` on the GIL build, the C JSON decoder holds the GIL for the whole parse. The watchdog can't run until the parse ends, so detection is late and the captured stack may no longer show the culprit.
  - On 3.15t there's no GIL, so the watchdog can capture mid-stall.
  - `faulthandler.dump_traceback_later(timeout, repeat=False)` uses a C-level thread that writes tracebacks **without** taking the GIL, so it catches the C-held case on the GIL build. The lab script `labs/k5/faulthandler_watchdog.py` re-arms it each beat (`cancel_dump_traceback_later` + arm) and compares.
- **Overhead:** at `interval_ms=50`, that's 20 cross-thread wakeups per second. The lab measures throughput with the watchdog on versus off (§7 run D).

### 5.9 `metrics.py`

A hand-written Prometheus text exposition (`# HELP`, `# TYPE`, then `name{labels} value`), with counters, gauges and fixed-bucket histograms as plain Python objects, all updated on the loop thread. The metric names and labels are defined in [spec/06-observability](../spec/06-observability.md). K5 implements the gateway's subset of them, and gets the request-duration histogram, request counters by route and status, the inflight gauge, the rejection counters and the loop-lag metrics added to spec/06 if they're missing there.

### 5.10 Config keys used

All `[gateway]` keys in spec/05 (including `watchdog.*`), plus `debug.enabled`, `labs.enabled`, `master.boot_timeout_s` and `master.graceful_timeout_s`. No new keys.

## 6. Steps

1. **`http11.py` (sans-IO) + exhaustive parser tests.**
   - *Docs first:* `concepts/http-1-1-basics.md` (including request smuggling).
   - *Tests:* one test per row of the §5.2 table; a Hypothesis round-trip (render a request → parse → equal); split-at-every-offset feeding through a test harness that mimics `readuntil`.
2. **`server.py` + `routes.py` against a fake KP client.**
   - *Docs:* `concepts/event-loops.md` (asyncio flavour, building on K1's selectors primer), `concepts/coroutines-and-tasks.md`, `python/asyncio-streams.md`, `python/asyncio-timeout.md`.
   - *Tests:* keep-alive, pipelining order, timeouts (header, idle), 405 + `Allow`, 404 for debug routes when disabled.
3. **`kpclient.py`.**
   - *Docs:* `python/async-await-deep-dive.md` (Futures, `set_result`, reader task patterns).
   - *Tests* against a real master: out-of-order responses matched correctly; master killed → pending requests fail fast with 503 → reconnect; request timeout → 504. Update spec/02 §5.2 with `MASTER_TIMEOUT`/`MASTER_UNAVAILABLE`.
4. **Gateway supervision in the master.**
   - *Docs:* add spec/04 §8.
   - *Tests:* the gateway starts after the first READY; a killed gateway is restarted with backoff; 5 failures → `gateway_gave_up` while KP still serves; graceful shutdown answers in-flight long-polls with 202.
5. **Backpressure.**
   - *Docs:* `concepts/backpressure.md`.
   - *Tests:* at `max_inflight=2`, a third concurrent request gets 429 immediately (measure that it took < 50 ms); `QUEUE_FULL` → 429 + `Retry-After`; `/healthz` stays reachable while overloaded.
6. **`/metrics`, `/healthz`, `/v1/stats`.**
   - *Tests:* the output parses as Prometheus text. A tiny parser in the test checks each line's format, so no dependency is needed.
7. **Watchdog + `/debug/loop` + `/debug/block` + `/debug/blocking-submit`.**
   - *Docs:* `concepts/blocking-the-event-loop.md`, `concepts/cancellation.md`, `concepts/taskgroup.md`, `python/asyncio-taskgroup.md`.
   - *Tests:* `/debug/block?ms=300` → exactly one `loop_stall` event whose stack contains the `time.sleep` line in `routes.py`; `loop_stall_recovered` has `stall_ms` ≥ 300; the histogram has a count in the ≥ 200 ms buckets.
8. **Lab + `asyncio ps` walkthrough + docs** (§7, the tour, the report). Calibrate `doc=large` and record the size.

## 7. Lab design

- **Question:** how much throughput and tail latency does a blocking call inside an async handler cost, does `asyncio.to_thread` recover it, and does the answer depend on *what kind* of blocking (GIL-releasing sleep versus GIL-holding CPU work) and on the build (3.15 versus 3.15t)?
- **Implementer's hypothesis:**
  - `work=sleep`: `block` mode caps throughput at about `1000 / work_ms` requests/s *for the whole gateway*, with p99 growing with concurrency. `thread` mode recovers throughput up to the to_thread pool size, on both builds.
  - `work=json`: `thread` mode helps less on 3.15, because the parse still holds the GIL, so other coroutines can't run in parallel with it. They do get to interleave, which still improves p99. It helps more on 3.15t.
  - The watchdog catches `sleep` stalls with the right stack on both builds. It catches `json` stalls with the right stack only on 3.15t, or with the faulthandler variant.
  - Research §2.6 reports +31–36% throughput and −24–27% p99. Our numbers will differ because the workload differs. We report ours next to theirs and don't expect a match.
- **Independent variables:**
  - mode ∈ {block, thread}
  - work ∈ {sleep (work_ms ∈ {5, 20}), json (doc=large)}
  - interpreter ∈ {3.15, 3.15t}
  - client concurrency ∈ {16, 64}
- **Dependent variables:**
  - requests/s
  - p50/p99/max latency (ms) from `labs/k5/http_load.py`
  - the loop-lag histogram and stall count (from `/debug/loop`)
  - collateral latency: a **probe** client hitting `GET /healthz` 10×/s during the run, recording its p99 (this shows that *unrelated* requests suffer)
  - gateway CPU utilization
- **Controls:**
  - `master.workers=4`, `sync` executor, task `labs.echo`
  - `gateway.max_inflight=1024` (so backpressure doesn't trigger during runs A/B)
  - logs at WARNING
  - run from the ext4 snapshot
  - fixed run duration of 20 s after a 3 s warm-up
- **Runs:**
  - **A**: block vs thread for `work=sleep`
  - **B**: block vs thread for `work=json`
  - **C**: backpressure, with `max_inflight=64`, client concurrency 256 and `POST /v1/jobs` → a measured 429 rate and p99 of accepted requests, compared with `max_inflight=100000` (effectively unbounded: watch p99 and gateway RSS grow)
  - **D**: watchdog overhead, plain `POST /v1/jobs` with the watchdog on vs off
  - **E**: a detector comparison on a planted 300 ms stall of each kind: our watchdog vs asyncio debug mode (`PYTHONASYNCIODEBUG=1`, `slow_callback_duration=0.1`) vs the faulthandler variant. Record detected yes/no, detection delay, and whether the culprit line was identified.
- **Repeats and statistics:** per [spec/10](../spec/10-measurement-methodology.md): 3 warm-ups, ≥ 10 repeats per cell, round-robin interleaving, and a fresh server per run. Latency percentiles use full samples (spec/10 §6). Comparisons use ratio-of-medians CIs (§4.3). The practical-significance threshold is stated in the Method section before the runs, as spec/10 §9 requires.
- **Procedure:** `./scripts/dev.sh lab k5-loop` runs `labs/k5/loop_lag_experiment.py` (spec/08 §1). `labs/k5/blocking_endpoints.md` explains in plain words what each blocking variant of `/debug/blocking-submit` simulates and which real library calls it stands for. For each cell it starts `kiln serve examples.demo:app --labs --debug` with `KILN_GATEWAY__ENABLED=true` (plus the cell's env overrides) under the cell's interpreter, waits for `/healthz` = 200, runs `http_load.py --url ... --concurrency C --duration 20 --warmup 3` and the probe, fetches `/debug/loop`, stops the server, and writes the ExperimentRecords.
- **`labs/k5/http_load.py`:** an asyncio client with C keep-alive connections, each sending requests back to back (closed-loop, `--concurrency C`). It also supports open-loop mode (`--rate R`), matching the KP load generator's two modes (spec/10 §6). Runs A–D use closed-loop. Run A is repeated once in open-loop mode at about 80% of the measured `thread`-mode throughput, to show coordinated omission (the primer `concepts/backpressure.md` explains it). It records per-request latency with `perf_counter_ns`, counts statuses, and fails the run if any non-2xx/429 status appears. It's a plain HTTP/1.1 writer and reader that reuses `kiln.gateway.http11` for response parsing (`parse_head` works on response status lines through a `response=True` flag; add this in step 8).
- **Live inspection walkthrough** (part of the report, not measured):
  1. During a run A `block` cell, run `python -m asyncio ps <gateway pid>` and `python -m asyncio pstree <gateway pid>` (G5.6).
  2. Paste the real output.
  3. Annotate which task is stuck and why the stack shows `time.sleep`.
- **Result tables** (headers only):
  - *A/B:* `work | interp | conc | mode | req/s [CI] | p50 ms | p99 ms [CI] | healthz probe p99 ms | stalls`
  - *Improvement:* `work | interp | conc | throughput ratio thread/block [CI] | p99 ratio [CI] | research §2.6 figure`
  - *C:* `max_inflight | conc | accepted req/s | 429 rate | p99 accepted ms | gateway peak RSS MiB`
  - *D:* `watchdog | req/s [CI] | ratio [CI]`
  - *E:* `stall kind | interp | detector | detected? | detection delay ms | culprit line shown?`

## 8. Verification gates (ADR-020)

| # | Claim to verify | How to check | If false → fallback |
|---|---|---|---|
| G5.1 | `StreamReader.readuntil` raises `LimitOverrunError` when the separator isn't found within `limit` | a unit test with a 20 KiB header block | read in chunks manually with a size counter; document it |
| G5.2 | `sys._current_frames()` includes the loop thread's frame from another thread | test: block the loop with `time.sleep`, then capture from the watchdog | use `faulthandler` as the primary detector instead; ADR |
| G5.3 | the C `json.loads` holds the GIL for the full parse on the GIL build (watchdog delayed) | run E on 3.15 | if the watchdog captures mid-parse anyway, record it, and the primer explains the observation |
| G5.4 | asyncio debug mode logs slow callbacks above `slow_callback_duration` | run E | record what it does log |
| G5.5 | `faulthandler.dump_traceback_later` dumps without the GIL (catches the C-held stall) | run E on 3.15 | record it; drop that comparison row |
| G5.6 | `python -m asyncio ps PID` / `pstree PID` exist on 3.14+/3.15 and need ptrace permission | run against the gateway with `ptrace_scope=1` (expect failure unless it's our descendant/sudo), then with `sudo` or `ptrace_scope=0` | if the CLI names differ, use the real ones and correct the errata. If ptrace is unavailable, document `sudo sysctl kernel.yama.ptrace_scope=0` (spec/07) |
| G5.7 | `asyncio.TaskGroup` and `asyncio.timeout` behave as documented (3.11+) | primer snippets run in tests | — |
| G5.8 | the default `to_thread` pool is `min(32, cpu_count+4)` threads on this machine | print `loop._default_executor` after the first `to_thread` (or `ThreadPoolExecutor()._max_workers`) | record the real number; it bounds run A's `thread` throughput |
| G5.9 | `writer.drain()` actually pauses when the transport's write buffer exceeds its high-water mark | test with a client that never reads: the server-side task stays blocked in `drain`, and RSS doesn't grow | document the real limits (`get_write_buffer_limits()`) |

## 9. Acceptance criteria

- **AC-5.1** Every parsing rule in §5.2 has a passing test (`tests/gateway/test_http11.py`), including the smuggling case (CL + TE → 400) and split-at-every-offset.
- **AC-5.2** Every route and every KP→HTTP error mapping in spec/02 §5.2 has an integration test against a real master (`tests/integration/test_gateway_routes.py`).
- **AC-5.3** Pipelined requests on one connection are answered in order (`test_pipelining_order`).
- **AC-5.4** Over-limit requests get 429 in < 50 ms without waiting, and `/healthz` stays 200 under overload.
- **AC-5.5** The master survives gateway death: restart with backoff, give up after 5, and KP service continues (`tests/integration/test_gateway_supervision.py`).
- **AC-5.6** `/debug/block?ms=300` produces exactly one `loop_stall` log with the culprit line in the stack, and one `loop_stall_recovered` with `stall_ms` ≥ 300.
- **AC-5.7** All `/debug/*` routes return 404 when `debug.enabled=false`.
- **AC-5.8** Lab runs A–E are complete (or `not measured` with a reason) and published to `results/published/K5/`. The `asyncio ps`/`pstree` walkthrough has real output.
- **AC-5.9** The Docs deliverables exist and `./scripts/dev.sh docs-check` passes.

## 10. Docs deliverables

| Kind | Path | Must cover |
|---|---|---|
| Concept | `docs/concepts/event-loops.md` | asyncio's loop on top of selectors (link to K1's selectors primer); ready queue, callbacks, `call_soon(_threadsafe)`; the self-pipe again |
| Concept | `docs/concepts/coroutines-and-tasks.md` | coroutine objects, Tasks, Futures, `await` as "yield to the loop"; why calling an `async def` doesn't run it |
| Concept | `docs/concepts/cancellation.md` | `CancelledError`, cooperative cancellation, `asyncio.timeout`, why swallowing `CancelledError` is a bug, links to K4 |
| Concept | `docs/concepts/taskgroup.md` | structured concurrency, `TaskGroup` failure semantics, `ExceptionGroup`/`except*` |
| Concept | `docs/concepts/http-1-1-basics.md` | request/response format, headers, Content-Length framing, keep-alive, pipelining, status codes we use, **request smuggling** |
| Concept | `docs/concepts/backpressure.md` | Little's law (intuition), unbounded queues → latency collapse, "a semaphore you await is a queue", 429 + Retry-After, `drain()` |
| Concept | `docs/concepts/blocking-the-event-loop.md` | what blocks (sync I/O, CPU, C calls holding the GIL); the effect on every connection; detection (our watchdog, debug mode, faulthandler, `asyncio ps`); the fixes (`to_thread`, processes, async libraries) |
| Python | `docs/python/async-await-deep-dive.md` | Futures, `set_result`/`set_exception`, `asyncio.wait`/`wait_for`, `gather` vs `TaskGroup` |
| Python | `docs/python/asyncio-streams.md` | `start_server`, `StreamReader.readuntil`/`readexactly`, `limit`, `StreamWriter.write`/`drain`/`close`/`wait_closed` |
| Python | `docs/python/asyncio-taskgroup.md` | syntax and semantics, `except*` |
| Python | `docs/python/asyncio-timeout.md` | `async with asyncio.timeout(...)`, `TimeoutError` |
| Python | `docs/python/contextvars.md` | **only if** contextvars are used (for example a per-request id in logs). If unused, don't write it |
| Tour | `docs/tours/K5.md` | stops: http11 → server loop → routes → kpclient reader task → backpressure → watchdog → master's GatewayHandle |
| Lab | `docs/labs/K5-blocked-loop.md` | per the lab template; runs A–E; the asyncio ps walkthrough |
| Lab notes | `labs/k5/blocking_endpoints.md` | what each `/debug/blocking-submit` variant simulates (sync HTTP client, a big JSON parse) and why `to_thread` helps or doesn't |
| Spec updates | `docs/spec/02-protocol.md` §5.2, `docs/spec/04-supervisor.md` §8, `docs/spec/06-observability.md` | the new route and error codes; gateway supervision; gateway metrics and events |

## 11. Reference comparison assignment

1. **h11** (`h11/_readers.py`, `_connection.py`): its sans-IO state machine compared with ours. *Questions:* how does h11 handle CL+TE? Which leniencies does it allow that we reject (bare LF? obs-fold?)? Why is sans-IO the shared idea?
2. **uvicorn** (`uvicorn/protocols/http/h11_impl.py`, `httptools_impl.py`): uvicorn uses asyncio *Protocols* (callbacks) instead of *Streams*. *Questions:* what does that buy in speed? How does uvicorn apply flow control (`pause_reading`/`resume_reading`)? How does `--limit-concurrency` compare with our `max_inflight`?
3. **aiodebug** (`log_slow_callbacks`) and **loopmon**: are they a monkeypatched `Handle._run`, an in-loop timer, or a separate thread? *Question:* which of them could catch the GIL-held C stall, and why?
4. **asyncio debug mode** source (`asyncio/base_events.py`, `_run_once` slow-callback logging): *Question:* why can it only report *after* the callback returns?

## 12. Risks and pitfalls

- **Load generator bottleneck.** A Python client may saturate before the gateway does. Check: run the client on 3.15t or with more processes. If client CPU is ~100% of a core, the measurement is client-bound, and the report must say so.
- **Accidental blocking in our own gateway** (for example synchronous logging to a slow file). Logging goes to stderr, which is fine; mention the risk in the primer.
- **`call_soon_threadsafe` after the loop closes** raises `RuntimeError` in the watchdog thread on shutdown. Stop the watchdog *before* closing the loop.
- **Port 7341 already in use** from a previous run. The test fixtures use port 0 (ephemeral), and the gateway reports its real port in the READY line.
- **Long-poll slots**: run C must not be confused by `wait` requests. Use `POST /v1/jobs` only in C.
- **ptrace**: `asyncio ps` fails silently or with `PermissionError` under `ptrace_scope=1`. G5.6 documents it.

## 13. Checkpoint quiz (seed questions)

1. A handler calls `time.sleep(0.2)`. 50 clients are connected. What happens to the other 49 during those 200 ms? What changes with `await asyncio.sleep(0.2)`?
2. Why is "the server is slow but nothing crashed" the typical symptom of a blocked loop, and why don't normal error logs catch it?
3. Our watchdog uses a separate thread. Why can't the check run *on* the loop itself?
4. Why can the watchdog print the culprit stack for `time.sleep` but maybe not for a giant `json.loads` on 3.15? What changes on 3.15t?
5. Why do we answer 429 immediately instead of waiting for a free slot? What happens to p99 and memory if we wait?
6. What is request smuggling, and which single rule in our parser prevents the classic form of it?
7. Why does handling one request at a time per connection automatically make pipelining responses come out in order?
8. The master dies while 100 HTTP requests are waiting on KP replies. What does each client receive, and how fast?
9. Why is the gateway started with spawn even when `start.method = "fork"`?
10. Name two things `python -m asyncio ps` shows that a log file wouldn't.

## 14. Stretch / later

- Re-implement the gateway's HTTP layer with asyncio Protocols (as uvicorn does) and compare throughput.
- Per-route `max_inflight`, so long-polls get a separate budget.
- Use `asyncio.capture_call_graph()` (3.14+) in `/debug/loop` to include task graphs with each stall.
