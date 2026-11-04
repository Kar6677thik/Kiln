"""`kiln bench load`: drive a running master with many jobs and record what happened.

WHY THIS MODULE EXISTS
    SC-1 ("10,000 jobs under load") and SC-2 ("zero dropped jobs during reload") need a tool
    that submits thousands of jobs, waits for every one, counts how each ended, measures
    latency and throughput, optionally sends SIGHUP every few seconds, and saves it all as an
    ExperimentRecord (docs/spec/10-measurement-methodology.md). This is that tool.

HOW IT FITS
    kiln bench load ──run_load(options)──▶ C asyncio connections ──submit + wait──▶ master
                                         ──(optional) SIGHUP every S seconds──▶ master pid
                    ──▶ ExperimentRecord in results/K1/<experiment>/<run_id>.json

    This is the learner's first look at asyncio: many connections waiting at once in one
    thread (docs/python/asyncio-first-look.md). The *master* doesn't use asyncio (ADR-006);
    a client may.

READ FIRST
    docs/python/asyncio-first-look.md, docs/spec/10-measurement-methodology.md §6 and §8

MILESTONE
    Introduced in K1.
"""

import asyncio
import math
import os
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from kiln.bench import records
from kiln.protocol.framing import FrameDecoder, encode_frame

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


@dataclass
class LoadOptions:
    """Everything one load run needs.

    Why:
        A single object to pass around and to copy into the record's params.
    """

    jobs: int
    concurrency: int | None
    rate: float | None
    task: str
    args: list[object]
    kwargs: dict[str, object]
    host: str = "127.0.0.1"
    port: int = 7340
    reload_every_s: float | None = None
    pidfile: str | None = None
    experiment: str = "load"
    variant: str = "default"
    results_dir: Path | None = None


@dataclass
class LoadResult:
    """What happened during a run.

    Why:
        Collected while running, then summarised into the ExperimentRecord.
    """

    states: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    retries: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    finish_times: list[float] = field(default_factory=list)
    reloads: list[float] = field(default_factory=list)


def _req_id(reply: dict[str, object]) -> int:
    """Read a reply's req_id (-1 if missing or not an integer).

    Args:
        reply: a reply from the master.

    Returns:
        The req_id.
    """
    value = reply.get("req_id")
    return value if isinstance(value, int) else -1


class AsyncKiln:
    """One asyncio KP/1 connection that can have many requests in flight (pipelining).

    Why:
        A request/response per connection at a time would need thousands of connections;
        req_ids let many requests share one connection (spec/02 §3).
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Wrap an open connection and start the background reader.

        Why:
            The background reader must start as soon as the connection exists, or replies would pile
            up unread.

        Args:
            reader: the stream reader.
            writer: the stream writer.
        """
        self.reader = reader
        self.writer = writer
        self.next_id = 1
        self.waiting: dict[int, asyncio.Future[dict[str, object]]] = {}
        self.decoder = FrameDecoder()
        self.reader_task = asyncio.ensure_future(self._read_forever())

    async def _read_forever(self) -> None:
        """Read replies and resolve the matching futures.

        Why:
            Replies can arrive in any order (a `wait` finishes later than a newer `submit`);
            the req_id says which request each reply answers.
        """
        while True:
            data = await self.reader.read(65536)
            if not data:
                for future in self.waiting.values():
                    if not future.done():
                        future.set_exception(ConnectionError("master closed the connection"))
                return
            for reply in self.decoder.feed(data):
                pending = self.waiting.pop(_req_id(reply), None)
                if pending is not None and not pending.done():
                    pending.set_result(reply)

    async def call(self, msg: dict[str, object]) -> dict[str, object]:
        """Send one request and wait for its reply.

        Why:
            Each request gets its own req_id and future, so many requests can share one connection
            and still get the right replies.

        Args:
            msg: the request without req_id.

        Returns:
            The reply (ok or error).
        """
        req_id = self.next_id
        self.next_id += 1
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self.waiting[req_id] = future
        self.writer.write(encode_frame({**msg, "req_id": req_id}))
        await self.writer.drain()
        return await future

    async def close(self) -> None:
        """Close the connection."""
        self.reader_task.cancel()
        self.writer.close()


def _substitute(value: object, index: int) -> object:
    """Replace the text "{i}" inside strings with the job index (K6 needs unique keys).

    Why:
        Some labs (K6's unbounded cache) need every job to have different arguments; a placeholder
        gives that without a script per run.

    Args:
        value: an argument value (nested lists/dicts are handled).
        index: the job number.

    Returns:
        The value with "{i}" replaced.
    """
    if isinstance(value, str):
        return value.replace("{i}", str(index))
    if isinstance(value, list):
        return [_substitute(item, index) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, index) for key, item in value.items()}
    return value


async def _one_job(
    client: AsyncKiln, options: LoadOptions, index: int, scheduled: float, result: LoadResult
) -> None:
    """Submit one job (retrying on QUEUE_FULL), wait for it, record how it ended.

    Why:
        One job's full life (submit, retry on QUEUE_FULL, wait, record) in one place, shared by the
        closed and open loops.

    Args:
        client: a connection.
        options: the run's options.
        index: the job number (for {i}).
        scheduled: when the job was *meant* to start (open loop measures from here).
        result: where to record the outcome.
    """
    submit = {
        "type": "submit",
        "task": options.task,
        "args": _substitute(options.args, index),
        "kwargs": _substitute(options.kwargs, index),
    }
    while True:
        reply = await client.call(submit)
        if reply.get("type") == "ok":
            break
        code = str(reply.get("code"))
        if code == "QUEUE_FULL":
            result.retries += 1
            await asyncio.sleep(float(str(reply.get("retry_after_s", 1.0))))
            continue
        result.errors[code] = result.errors.get(code, 0) + 1
        return
    job_id = reply["job_id"]
    while True:
        done = await client.call({"type": "wait", "job_id": job_id, "timeout_s": 300})
        job = done.get("job")
        if done.get("type") == "ok" and not done.get("timed_out") and isinstance(job, dict):
            break
    state = str(job.get("state"))
    result.states[state] = result.states.get(state, 0) + 1
    finished = time.monotonic()
    result.latencies_ms.append((finished - scheduled) * 1000)
    result.finish_times.append(finished)


async def _closed_loop(clients: list[AsyncKiln], options: LoadOptions, result: LoadResult) -> None:
    """C connections, each running jobs back to back (the next starts when one ends).

    Args:
        clients: the connections.
        options: the run's options.
        result: where outcomes are recorded.
    """
    # WHY: one shared iterator: each connection takes the next unused job number.
    counter = iter(range(options.jobs))
    await asyncio.gather(*(_closed_worker(client, counter, options, result) for client in clients))


async def _closed_worker(
    client: AsyncKiln, counter: Iterator[int], options: LoadOptions, result: LoadResult
) -> None:
    """One connection's closed loop: take the next job number, run it, repeat.

    Args:
        client: this connection.
        counter: the shared job-number iterator.
        options: the run's options.
        result: where outcomes are recorded.
    """
    for index in counter:
        await _one_job(client, options, index, time.monotonic(), result)


async def _open_loop(clients: list[AsyncKiln], options: LoadOptions, result: LoadResult) -> None:
    """Start jobs on a fixed schedule of `rate` per second, whatever the replies do.

    Why:
        Measuring latency from the *scheduled* start avoids "coordinated omission": a closed
        loop stops sending while the server stalls and so hides the stall (spec/10 §6).

    Args:
        clients: the connections (used round-robin).
        options: the run's options (rate is set).
        result: where outcomes are recorded.
    """
    assert options.rate  # INVARIANT: open loop only runs with --rate
    start = time.monotonic()
    tasks = []
    for index in range(options.jobs):
        scheduled = start + index / options.rate
        delay = scheduled - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        client = clients[index % len(clients)]
        tasks.append(asyncio.ensure_future(_one_job(client, options, index, scheduled, result)))
    await asyncio.gather(*tasks)


def _read_pid(pidfile: str | None) -> int | None:
    """Read the master's pid from its pidfile.

    Why:
        Reloads and CPU measurements need the master's pid; a missing pidfile should disable those
        features, not crash the run.

    Args:
        pidfile: the path, or None.

    Returns:
        The pid, or None.
    """
    if not pidfile:
        return None
    try:
        return int(Path(pidfile).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _cpu_seconds(pid: int | None) -> float | None:
    """Read a process's CPU time (user + system) from /proc/<pid>/stat.

    Why:
        The master's CPU time before and after a run tells how close the single-threaded master is
        to its ceiling (K1 lab c).

    Args:
        pid: the process, or None.

    Returns:
        Seconds of CPU used so far, or None.
    """
    if pid is None:
        return None
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    # WHY: field 2 (the name) can contain spaces, so split after its closing ")".
    fields = text.rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / CLOCK_TICKS


async def _reloader(pid: int, every_s: float, result: LoadResult, stop: asyncio.Event) -> None:
    """Send SIGHUP to the master every `every_s` seconds until stopped.

    Why:
        SC-2 needs reloads to happen *during* load; a separate task sends them on a fixed schedule
        while jobs run.

    Args:
        pid: the master's pid.
        every_s: the interval.
        result: reload timestamps are recorded here.
        stop: set when the load is done.
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except TimeoutError:
            os.kill(pid, signal.SIGHUP)
            result.reloads.append(time.monotonic())


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile (spec/10 §4.1).

    Why:
        p50/p90/p99 summarise thousands of latencies; nearest-rank is the simple, exact definition
        spec/10 §4.1 uses.

    Args:
        values: the samples.
        fraction: such as 0.99.

    Returns:
        The value at that rank (0.0 if there are no samples).

    Example:
        >>> percentile([1.0, 2.0, 3.0, 4.0], 0.5)
        2.0
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    # WHY: nearest rank = the smallest value with at least `fraction` of samples at or below it.
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


async def _run(options: LoadOptions) -> tuple[LoadResult, float]:
    """Open connections, run the load, return the result and the wall duration.

    Why:
        All the asyncio work in one coroutine, so run_load() can start it with a single
        asyncio.run().

    Args:
        options: the run's options.

    Returns:
        (result, duration in seconds).
    """
    count = options.concurrency or 64
    clients = []
    for _ in range(count):
        reader, writer = await asyncio.open_connection(options.host, options.port)
        clients.append(AsyncKiln(reader, writer))
    result = LoadResult()
    stop = asyncio.Event()
    pid = _read_pid(options.pidfile)
    reloader = None
    if options.reload_every_s and pid is not None:
        reloader = asyncio.ensure_future(_reloader(pid, options.reload_every_s, result, stop))
    started = time.monotonic()
    if options.rate:
        await _open_loop(clients, options, result)
    else:
        await _closed_loop(clients, options, result)
    duration = time.monotonic() - started
    stop.set()
    if reloader is not None:
        await reloader
    for client in clients:
        await client.close()
    return result, duration


def steady_throughput(finish_times: list[float]) -> float:
    """Jobs per second between the 10th and 90th percentile completion times.

    Why:
        spec/10 §8: excluding ramp-up (connections opening) and drain (the last stragglers)
        measures the steady state the system can sustain.

    Args:
        finish_times: monotonic completion times.

    Returns:
        Jobs per second (0.0 if too few jobs).
    """
    if len(finish_times) < 10:
        return 0.0
    ordered = sorted(finish_times)
    low = ordered[len(ordered) // 10]
    high = ordered[(len(ordered) * 9) // 10]
    count = (len(ordered) * 9) // 10 - len(ordered) // 10
    return count / (high - low) if high > low else 0.0


def run_load(options: LoadOptions) -> tuple[dict[str, object], Path]:
    """Run one load test and save it as an ExperimentRecord.

    Why:
        Every load run must end as an ExperimentRecord (ADR-022), so lab reports can cite exact
        numbers.

    Args:
        options: the run's options.

    Returns:
        (the record, the path it was written to).
    """
    pid = _read_pid(options.pidfile)
    cpu_before = _cpu_seconds(pid)
    result, duration = asyncio.run(_run(options))
    cpu_after = _cpu_seconds(pid)
    params: dict[str, object] = {
        "jobs": options.jobs,
        "mode": "open" if options.rate else "closed",
        "concurrency": options.concurrency,
        "rate": options.rate,
        "task": options.task,
        "kwargs": options.kwargs,
        "reload_every_s": options.reload_every_s,
    }
    record = records.new_record("K1", options.experiment, options.variant, params, 0)
    record["duration_s"] = round(duration, 3)
    for state in ("SUCCEEDED", "FAILED", "TIMED_OUT", "LOST", "CANCELLED"):
        records.add_metric(
            record, f"jobs_{state.lower()}", "count", value=result.states.get(state, 0)
        )
    records.add_metric(record, "submit_errors", "count", value=sum(result.errors.values()))
    records.add_metric(record, "queue_full_retries", "count", value=result.retries)
    records.add_metric(record, "reloads", "count", value=len(result.reloads))
    records.add_metric(
        record, "throughput", "jobs/s", value=round(steady_throughput(result.finish_times), 1)
    )
    records.add_metric(
        record,
        "throughput_overall",
        "jobs/s",
        value=round(len(result.finish_times) / duration, 1) if duration else 0.0,
    )
    latency = [round(value, 3) for value in result.latencies_ms]
    records.add_metric(record, "latency", "ms", samples=latency)
    metrics = record["metrics"]
    assert isinstance(metrics, dict)  # INVARIANT: new_record() creates a dict
    metrics["latency"]["summary"] = {
        name: round(percentile(latency, frac), 3)
        for name, frac in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99), ("max", 1.0))
    }
    if cpu_before is not None and cpu_after is not None:
        records.add_metric(record, "master_cpu", "s", value=round(cpu_after - cpu_before, 3))
    if result.errors:
        record["notes"] = f"submit errors by code: {result.errors}"
    return record, records.write_record(record, options.results_dir)
