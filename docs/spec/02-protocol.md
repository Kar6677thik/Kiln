# 02 — Protocol: Exactly What Bytes Go Over the Wire

> **Status:** Approved spec. **Depends on:** [01-architecture](01-architecture.md).
> **Concepts:** bytes vs. text, UTF-8, JSON, TCP streams, framing, big-endian integers. The K0/K1 primers cover these. `docs/concepts/framing.md` is written in K1.

A **protocol** is an agreement about what bytes mean. Kiln has two:

- **KP/1** ("Kiln Protocol version 1"). It is used by clients ↔ master, master ↔ workers, master ↔ forkserver and gateway ↔ master.
- **HTTP/1.1 subset.** It is used by the outside world ↔ gateway (K5).

---

## 1. Why framing is needed (read this if TCP is new)

TCP gives you a **stream** of bytes, like water through a pipe, with no gaps between messages. Suppose you send `{"a":1}` and then `{"b":2}`. The other side might receive `{"a":1}{"b":2}` in one `recv()`, or `{"a":` and then `1}{"b":2}`. TCP never tells you where one message ends.

**Framing** is how we mark the boundaries. Kiln uses **length-prefix framing**. Before each message we send its length as a fixed 4-byte number. The receiver reads 4 bytes to learn the length N, then waits until it has N more bytes. Those N bytes are exactly one message. [ADR-008](../decisions.md#adr-008) explains why we chose this over newline-delimited JSON.

## 2. KP/1 frame format

```
 0               1               2               3               4 ...
 ├───────────────┴───────────────┴───────────────┴───────────────┼──────────────── N bytes ───────────────┤
 │        N  (unsigned 32-bit integer, big-endian, "network order")│   UTF-8 encoded JSON object            │
 └───────────────────────────────────────────────────────────────┴────────────────────────────────────────┘
```

| Rule | Value |
|---|---|
| Length prefix | 4 bytes, unsigned, **big-endian**: `struct.pack("!I", n)` |
| Payload | UTF-8 JSON that decodes to a **JSON object** (a dict). Arrays, numbers and strings at top level are invalid |
| Max payload | `protocol.max_frame_bytes`, default **1,048,576** (1 MiB) |
| Zero-length frame | invalid |
| JSON details | `json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. NaN and Infinity are **not** allowed, because they aren't valid JSON |
| Every message has | `"type"`: a string from the tables below |
| Unknown fields | **ignored** by the receiver (this is "forward compatibility") |
| Unknown `type` | error `BAD_REQUEST` (client channel), or a logged warning that drops the message (IPC) |

### 2.1 What a receiver does on bad input

| Problem | Client connection (master side) | Worker channel |
|---|---|---|
| Length > max | send `error{code:"PAYLOAD_TOO_LARGE"}`, then **close** the connection. We can't skip the bytes safely | log `CRITICAL`, kill the worker (a protocol bug) |
| Length 0, invalid UTF-8, invalid JSON, or not an object | send `error{code:"BAD_FRAME"}`, then close | same as above |
| Valid frame but bad fields | send `error{code:"BAD_REQUEST", message:"…which field…"}`, and keep the connection open | log `ERROR`, ignore the message |

### 2.2 The reference decoder (a behaviour spec, not code)

`FrameDecoder` is an object you `feed(data: bytes)` into. It returns a list of complete payload dicts and keeps the leftover bytes for next time. It must pass these tests, which K1 writes:

- Feeding a valid frame **one byte at a time** returns it exactly once, on the last byte.
- Feeding three frames concatenated in one call returns all three, in order.
- A frame split across calls at **every possible offset** decodes the same way. This is a Hypothesis property test.
- An oversize length raises `FrameTooLarge` as soon as the 4 header bytes arrive, without waiting for the body.

## 3. Client ↔ master messages (KP/1 client channel)

Every **request** carries `req_id`, an integer that the client picks (it should be unique per connection). Every **response** echoes the same `req_id`. This lets a client send many requests without waiting for each answer (this is called *pipelining*) and still match up the replies. Responses on one connection may arrive **out of order**: a `wait` might answer after a later `ping`.

### 3.1 Requests

| `type` | Fields | Meaning |
|---|---|---|
| `ping` | — | health check. Reply `ok{pong:true, server_time_ms}` |
| `submit` | `task` (str), `args` (list, default `[]`), `kwargs` (object, default `{}`), `timeout_s` (number, optional), `max_attempts` (int ≥ 1, optional) | queue a job. Reply `ok{job_id, state:"QUEUED"}` |
| `status` | `job_id` | reply `ok{job:<JobView>}` |
| `wait` | `job_id`, `timeout_s` (number, default 30, max 300) | reply as soon as the job is terminal: `ok{job:<JobView>}`. If the timeout passes first, reply `ok{job:<JobView>, timed_out:true}`. The job's state is then still non-terminal; it's the *wait* that timed out, not the job |
| `cancel` | `job_id` | only for `QUEUED` jobs. Reply `ok{job:<JobView>}` (state `CANCELLED`), or `error{code:"NOT_CANCELLABLE"}` |
| `stats` | — | reply `ok{stats:<StatsView>}`: queue depth, counters per state, worker table summary, uptime |
| `debug` | `target` (`"master"` or worker id int), `op` (str), `params` (object) | only when `debug.enabled = true`. It is forwarded to the worker and the reply relayed back. The ops are defined in K6/K7 |

### 3.2 Responses

```json
{"type":"ok",    "req_id":7, "job_id":"6f1c…"}
{"type":"error", "req_id":7, "code":"QUEUE_FULL", "message":"queue has 50000/50000 jobs", "retry_after_s":1.0}
```

### 3.3 Error codes (the complete list; add new ones here first)

| Code | When | Client should... |
|---|---|---|
| `BAD_FRAME` | undecodable frame | fix the client. The connection gets closed |
| `PAYLOAD_TOO_LARGE` | frame > max | send smaller args. The connection gets closed |
| `BAD_REQUEST` | missing or wrong-typed field, unknown `type` | fix the request |
| `UNKNOWN_TASK` | `task` is not in the current manifest | check the task name |
| `NOT_FOUND` | unknown `job_id`, or one evicted after the TTL | expect results to expire |
| `QUEUE_FULL` | queue length ≥ `queue.max_size` | back off for `retry_after_s` and retry |
| `NOT_CANCELLABLE` | the job isn't `QUEUED` | nothing; it's already running or done |
| `SHUTTING_DOWN` | the master is in graceful shutdown | go away |
| `NOT_READY` | no worker has reported `ready` yet (see §3.4) | retry shortly |
| `DEBUG_DISABLED` | `debug` request while debug is off | enable it in config |
| `INTERNAL` | a bug in Kiln (the exception is logged) | report it |

### 3.4 Accepting connections before workers are ready

The master opens its listening socket **after** the first worker reports `ready` ([ADR-013](../decisions.md#adr-013)). It needs that worker's task manifest to validate `submit`. A client that connects too early gets "connection refused". That's simple and honest. `kiln submit` retries with backoff for up to 10 s. The `NOT_READY` code exists only for the rare window during a full reload in which the manifest is being replaced.

### 3.5 `JobView` (how a job is shown to clients)

```json
{
  "job_id": "6f1c2a…",                  // 32 lowercase hex chars (uuid4().hex)
  "task": "add",
  "state": "SUCCEEDED",                  // see 03-jobs-and-tasks §4
  "args": [2, 3], "kwargs": {},
  "submitted_at_ms": 1762183200123,      // wall-clock (time.time()*1000), for humans only
  "started_at_ms": 1762183200125,        // null until RUNNING
  "finished_at_ms": 1762183200126,       // null until terminal
  "attempts": [
    {"n": 1, "worker_id": 2, "pid": 4312, "outcome": "succeeded", "duration_ms": 0.8}
  ],
  "result": 5,                           // present only if SUCCEEDED
  "error": null                          // object {kind, type, message, traceback} if FAILED/TIMED_OUT/LOST
}
```

`error.kind` is one of: `exception` (the task raised), `timeout`, `worker_lost`, `unknown_task`, `serialization` (the result wasn't JSON-serializable), `cancelled`.

## 4. Master ↔ worker messages (KP/1 IPC)

They use the same framing. There's no `req_id`, except on `debug`/`debug_reply`.

### 4.1 Worker → master

| `type` | Fields | When |
|---|---|---|
| `ready` | `worker_id`, `pid`, `generation`, `capacity`, `executor`, `manifest` (see below), `python` {`version`, `implementation`, `gil_enabled`, `free_threaded_build`} | once, after boot. Before this the worker gets no jobs |
| `heartbeat` | `rss_bytes`, `jobs_done`, `inflight`, `gil_enabled` | every `worker.heartbeat_interval_s`, sent from the heartbeat thread |
| `result` | `job_id`, `attempt`, `ok` (bool), `value` (if ok), `error` {`type`, `message`, `traceback`} (if not), `duration_ms`, `timed_out` (bool, default false; K4) | one per `run`. `timed_out: true` means the worker cancelled the job cooperatively at its timeout (asyncio executor). The master treats it as a `timeout` failure but does **not** kill the worker |
| `retiring` | `reason`: `max_jobs` or `max_rss` | the worker decided to leave. It finishes in-flight work, then exits 0 |
| `boot_failed` | `error` {`type`, `message`, `traceback`} | the app import failed. The worker then exits with code **3** |
| `debug_reply` | `req_id`, `ok`, `data` or `error` | answers to `debug` |

`manifest` looks like `{"tasks": {"add": {"timeout_s": 30.0, "max_attempts": 1, "is_async": false}}}`.

### 4.2 Master → worker

| `type` | Fields | Meaning |
|---|---|---|
| `run` | `job_id`, `attempt`, `task`, `args`, `kwargs`, `timeout_s` | execute. The master only sends this when the worker has a free slot |
| `shutdown` | `graceful` (bool) | graceful: accept no more `run`s, finish in-flight work, exit 0. Not graceful: exit now (rarely used, since the master normally uses SIGKILL for that) |
| `debug` | `req_id`, `op`, `params` | K6/K7 diagnostics |

### 4.3 Master ↔ forkserver (K2)

| Direction | `type` | Fields | Notes |
|---|---|---|---|
| m → fs | `spawn_worker` | `worker_id`, `generation`, `worker_config` (dict) | **plus 1 file descriptor**, the worker's end of its socketpair, sent with `socket.send_fds` in the same `sendmsg` call |
| fs → m | `spawned` | `worker_id`, `pid` | |
| fs → m | `child_exit` | `pid`, `wait_status` (the raw int from `waitpid`) | the master decodes it exactly as if it had reaped the child itself |
| m → fs | `shutdown` | — | the forkserver exits once it has no children left |

**fd passing gotcha:** the fd travels in the *ancillary data* of `sendmsg`. Kiln sends the JSON frame in the **same** `sendmsg` call as the fd. On the forkserver side, `recv_fds` is used for the 4-byte header read, and the fd list is attached to the frame that follows. K2's primer `docs/concepts/fd-passing.md` must explain this in detail.

## 5. Gateway HTTP API (K5)

It is a hand-written subset of HTTP/1.1 ([ADR-014](../decisions.md#adr-014)). It listens on `127.0.0.1:7341`.

### 5.1 What the HTTP parser supports

| Feature | Supported? | If a client uses it |
|---|---|---|
| Methods GET, POST, DELETE | yes | — |
| Other methods | no | `405 Method Not Allowed` |
| `Content-Length` bodies | yes, max `gateway.max_body_bytes` (1 MiB) | over the limit: `413` |
| `Transfer-Encoding: chunked` | **no** | `501 Not Implemented` |
| Keep-alive (HTTP/1.1 default) | yes. Idle timeout `gateway.keepalive_timeout_s` (5 s) | — |
| `Connection: close` | yes | close after the response |
| Pipelining | yes, answered strictly in order | — |
| Header block size | max 16 KiB, read timeout `gateway.header_timeout_s` (10 s) | `431` / close |
| HTTP/1.0 | parsed. No keep-alive unless `Connection: keep-alive` is sent | — |
| HTTP/2, TLS, `Expect: 100-continue` | no | `100-continue` is ignored and the body is read normally |

### 5.2 Routes

| Method & path | Body / query | Success | Errors |
|---|---|---|---|
| `POST /v1/jobs` | JSON `{task, args?, kwargs?, timeout_s?, max_attempts?}` | `202` + `{job_id, state}` + `Location: /v1/jobs/<id>` | `400` bad JSON or fields, `404` unknown task, `413` too large, `429` queue full **or** gateway overloaded (with a `Retry-After` header), `503` shutting down or not ready |
| `GET /v1/jobs/<id>` | — | `200` + JobView | `404` |
| `GET /v1/jobs/<id>/wait?timeout_s=N` | N ≤ 60 | `200` + JobView if terminal. `202` + JobView if still pending when the timeout ends | `404` |
| `DELETE /v1/jobs/<id>` | — | `200` + JobView (CANCELLED) | `404`, `409` not cancellable |
| `GET /v1/stats` | — | `200` + StatsView | — |
| `GET /healthz` | — | `200 ok` if the master answers `ping` within 1 s, else `503` | — |
| `GET /metrics` | — | `200` Prometheus text format (see [06-observability](06-observability.md)) | — |
| `GET /debug/loop` | — | watchdog stats + the last 10 stalls with their stacks | `404` if debug is disabled |
| `POST /debug/block?ms=N` | — | **deliberately** calls `time.sleep(N/1000)` on the event loop (a K5 lab tool) | `404` if debug is disabled |
| `POST /debug/workers/<id>/<op>` | JSON params | relays to a KP `debug` request | `404` if debug is disabled |
| `POST /debug/blocking-submit` | same body as `POST /v1/jobs` | lab-only (K5 §5.7). It submits a job while doing a deliberately **blocking** call on the loop, to show the before and after of `asyncio.to_thread` | `404` if debug is disabled |

Mapping KP error codes to HTTP status: `BAD_REQUEST`→400, `UNKNOWN_TASK`→404, `NOT_FOUND`→404, `PAYLOAD_TOO_LARGE`→413, `QUEUE_FULL`→429, `NOT_CANCELLABLE`→409, `SHUTTING_DOWN`/`NOT_READY`→503, `DEBUG_DISABLED`→404, `INTERNAL`→500.

**Gateway-only error codes** (these never appear on KP; K5):

| Code | HTTP | When |
|---|---|---|
| `GATEWAY_OVERLOADED` | 429 + `Retry-After: 1` | the gateway already has `gateway.max_inflight` requests being processed. It refuses at once and never waits in a queue |
| `MASTER_TIMEOUT` | 504 | a KP request to the master got no reply within the gateway's request timeout |
| `MASTER_UNAVAILABLE` | 503 | the gateway's KP connection to the master is down (it reconnects with backoff) |

Every error body is JSON: `{"error": {"code": "...", "message": "..."}}`.

## 6. Versioning

The protocol version is `1`. A future incompatible change would become `KP/2` and need its own handshake. That is out of scope; it's written down here only so nobody invents a handshake now.
