"""Kiln's configuration: defaults, a TOML file, environment variables and CLI flags, merged.

WHY THIS MODULE EXISTS
    Kiln has dozens of settings (worker count, timeouts, limits...). They can come from four
    places, and the rules for who wins must be the same everywhere (docs/spec/05 §1):
    defaults < kiln.toml < KILN_SECTION__KEY environment variables < command-line flags.
    This module does the merge once, checks *every* value, reports *all* problems together
    (a typo like `max_job = 5` is an error, never silently ignored), and hands back one
    read-only Config object that is passed explicitly to everything that needs it.

HOW IT FITS
    cli.py ──load_config(path, os.environ, flag_overrides)──▶ Config (frozen dataclasses)
        ──▶ master/arbiter.py ──(inherited by fork, or Config.to_json() for spawn in K2)──▶ workers

READ FIRST
    docs/spec/05-configuration-and-cli.md, docs/python/dataclasses.md,
    docs/python/slots-and-frozen-dataclasses.md, docs/python/tomllib.md

MILESTONE
    Introduced in K1. Keys whose feature arrives later are validated now but marked
    "NOT YET USED (Kn)" in the RULES table.
"""

import json
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from kiln.errors import KilnError

ENV_PREFIX = "KILN_"


class ConfigError(KilnError):
    """One or more configuration values are wrong; the message lists every problem.

    Why:
        Fixing config one error at a time is miserable; collecting them all is kinder
        (docs/standards/documentation.md §4.6). The CLI turns this into exit code 2.
    """

    def __init__(self, problems: list[str]) -> None:
        """Store the problems and build a message with one problem per line.

        Args:
            problems: each a sentence saying what's wrong and how to fix it.
        """
        super().__init__("invalid configuration:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Rule:
    """How one setting is checked: its type, default and allowed range or choices.

    Why:
        One table row per setting (RULES below) is easier to read and test than validation
        code scattered through the program.
    """

    kind: str  # "int", "float", "bool", "str", "list"
    default: object
    minimum: float | None = None
    choices: tuple[str, ...] = ()
    strictly_positive: bool = False


# The complete key list of docs/spec/05 §2, as "section.key" → Rule.
RULES: dict[str, Rule] = {
    "master.bind": Rule("str", "127.0.0.1:7340"),
    "master.allow_non_loopback": Rule("bool", False),
    "master.workers": Rule("int", 4, minimum=1),
    "master.boot_timeout_s": Rule("float", 30.0, strictly_positive=True),
    "master.graceful_timeout_s": Rule("float", 30.0, strictly_positive=True),
    "master.boot_failure_limit": Rule("int", 5, minimum=1),
    "master.pidfile": Rule("str", ""),
    "app.spec": Rule("str", ""),
    "app.preload": Rule("bool", True),
    "app.pythonpath": Rule("list", []),
    "start.method": Rule("str", "fork", choices=("fork", "spawn", "forkserver")),
    "start.forkserver_preload": Rule("list", ["kiln"]),
    "worker.executor": Rule("str", "sync", choices=("sync", "threads", "interpreters", "asyncio")),
    "worker.executor_concurrency": Rule("int", 8, minimum=1),  # NOT YET USED (K4)
    "worker.heartbeat_interval_s": Rule("float", 1.0, strictly_positive=True),
    "worker.heartbeat_timeout_s": Rule("float", 10.0, strictly_positive=True),
    "worker.max_jobs": Rule("int", 0, minimum=0),
    "worker.max_jobs_jitter": Rule("int", 0, minimum=0),
    "worker.max_rss_mb": Rule("int", 0, minimum=0),
    "worker.memory_metric": Rule("str", "rss", choices=("rss", "pss", "uss")),
    "worker.gc_mode": Rule(
        "str", "default", choices=("default", "disabled", "threshold0", "freeze")
    ),  # NOT YET USED (K3)
    "worker.exit_via_os_exit": Rule("bool", False),  # NOT YET USED (K3)
    "queue.max_size": Rule("int", 50_000, minimum=1),
    "queue.retry_after_s": Rule("float", 1.0, minimum=0),
    "jobs.default_timeout_s": Rule("float", 30.0, strictly_positive=True),
    "jobs.default_max_attempts": Rule("int", 1, minimum=1),
    "jobs.cancel_grace_s": Rule("float", 2.0, minimum=0),  # NOT YET USED (K4)
    "jobs.result_ttl_s": Rule("float", 600.0, strictly_positive=True),
    "jobs.max_retained": Rule("int", 200_000, minimum=1),
    "protocol.max_frame_bytes": Rule("int", 1_048_576, minimum=64),
    "protocol.validator": Rule("str", "python", choices=("python", "native")),  # NOT YET USED (K8)
    "gateway.enabled": Rule("bool", False),  # NOT YET USED (K5) — this whole section
    "gateway.bind": Rule("str", "127.0.0.1:7341"),
    "gateway.max_inflight": Rule("int", 1024, minimum=1),
    "gateway.master_connections": Rule("int", 4, minimum=1),
    "gateway.max_body_bytes": Rule("int", 1_048_576, minimum=1),
    "gateway.header_timeout_s": Rule("float", 10.0, strictly_positive=True),
    "gateway.keepalive_timeout_s": Rule("float", 5.0, strictly_positive=True),
    "gateway.watchdog.enabled": Rule("bool", True),
    "gateway.watchdog.interval_ms": Rule("int", 50, minimum=1),
    "gateway.watchdog.threshold_ms": Rule("int", 100, minimum=1),
    "debug.enabled": Rule("bool", False),
    "labs.enabled": Rule("bool", False),
    "labs.preload_mb": Rule("int", 0, minimum=0),  # NOT YET USED (K3)
    "labs.gc_watch": Rule("bool", False),  # NOT YET USED (K3)
    "log.level": Rule("str", "INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    "log.format": Rule("str", "kv", choices=("kv", "json")),
    "log.file": Rule("str", ""),
}


# --- The Config object: one frozen dataclass per section ---------------------------------------


@dataclass(frozen=True, slots=True)
class MasterConfig:
    """Settings for the master process ([master] in kiln.toml).

    Why:
        Grouping by section mirrors the TOML file, so `config.master.workers` reads like the file.
    """

    bind: str
    allow_non_loopback: bool
    workers: int
    boot_timeout_s: float
    graceful_timeout_s: float
    boot_failure_limit: int
    pidfile: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Which app to load and how ([app]).

    Why:
        The app spec ("module:attribute") is the user's code; preload decides whether the
        master imports it before forking (needed for copy-on-write sharing, K3).
    """

    spec: str
    preload: bool
    pythonpath: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StartConfig:
    """How workers are created ([start]; K2).

    Why:
        Validated from K1 so K2 doesn't have to restructure the config.
    """

    method: str
    forkserver_preload: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Settings each worker uses ([worker]).

    Why:
        Heartbeats, recycling limits and (later) executor and GC choices.
    """

    executor: str
    executor_concurrency: int
    heartbeat_interval_s: float
    heartbeat_timeout_s: float
    max_jobs: int
    max_jobs_jitter: int
    max_rss_mb: int
    memory_metric: str
    gc_mode: str
    exit_via_os_exit: bool


@dataclass(frozen=True, slots=True)
class QueueConfig:
    """The master's queue limits ([queue]).

    Why:
        A bounded queue is backpressure: QUEUE_FULL instead of unbounded memory growth.
    """

    max_size: int
    retry_after_s: float


@dataclass(frozen=True, slots=True)
class JobsConfig:
    """Default job options and result retention ([jobs]).

    Why:
        Tasks and submits may override timeout and attempts; these are the fallbacks.
    """

    default_timeout_s: float
    default_max_attempts: int
    cancel_grace_s: float
    result_ttl_s: float
    max_retained: int


@dataclass(frozen=True, slots=True)
class ProtocolConfig:
    """KP/1 limits ([protocol]).

    Why:
        max_frame_bytes protects every process from huge frames.
    """

    max_frame_bytes: int
    validator: str


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """The HTTP gateway ([gateway] and [gateway.watchdog]; K5).

    Why:
        Validated from K1 so K5 doesn't have to restructure the config.
    """

    enabled: bool
    bind: str
    max_inflight: int
    master_connections: int
    max_body_bytes: int
    header_timeout_s: float
    keepalive_timeout_s: float
    watchdog_enabled: bool
    watchdog_interval_ms: int
    watchdog_threshold_ms: int


@dataclass(frozen=True, slots=True)
class DebugConfig:
    """Debug operations switch ([debug]).

    Why:
        Off by default: debug ops can dump memory (ADR-021).
    """

    enabled: bool


@dataclass(frozen=True, slots=True)
class LabsConfig:
    """Lab-only features ([labs]).

    Why:
        Lab tasks can crash or leak on purpose, so they're off unless asked for (ADR-021).
    """

    enabled: bool
    preload_mb: int
    gc_watch: bool


@dataclass(frozen=True, slots=True)
class LogConfig:
    """Logging settings ([log]).

    Why:
        Level, format (kv or json) and destination for every Kiln process (spec/06 §1).
    """

    level: str
    format: str
    file: str


@dataclass(frozen=True, slots=True)
class Config:
    """The complete, validated configuration.

    Why:
        One read-only object, created once in the master and passed explicitly (code-style §6:
        no global config). Frozen means nothing can change it by accident halfway through.
    """

    master: MasterConfig
    app: AppConfig
    start: StartConfig
    worker: WorkerConfig
    queue: QueueConfig
    jobs: JobsConfig
    protocol: ProtocolConfig
    gateway: GatewayConfig
    debug: DebugConfig
    labs: LabsConfig
    log: LogConfig

    def to_json(self) -> str:
        """Serialize to JSON, for passing to a spawned worker (K2).

        Returns:
            A JSON string that from_json() turns back into an equal Config.
        """
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Config":
        """Rebuild a Config from to_json() output, validating it again.

        Why:
            Spawned workers (K2) get the config as JSON text; re-validating it guards against a
            corrupted or mismatched copy.

        Args:
            text: JSON from to_json().

        Returns:
            An equal Config.
        """
        nested = json.loads(text)
        flat: dict[str, object] = {}
        for section, values in nested.items():
            for key, value in values.items():
                flat[_dataclass_key_to_rule_key(section, key)] = value
        return build_config(flat)


def _dataclass_key_to_rule_key(section: str, key: str) -> str:
    """Map a dataclass field back to its "section.key" name (watchdog_* fields are nested).

    Args:
        section: such as "gateway".
        key: such as "watchdog_enabled".

    Returns:
        Such as "gateway.watchdog.enabled".
    """
    if section == "gateway" and key.startswith("watchdog_"):
        return f"gateway.watchdog.{key.removeprefix('watchdog_')}"
    return f"{section}.{key}"


# --- Loading and merging -----------------------------------------------------------------------


def _flatten(table: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    """Turn nested TOML tables into dotted keys: {"master": {"workers": 4}} → {"master.workers": 4}.

    Why:
        Every source (file, env, CLI) is reduced to the same flat "section.key" shape, so
        merging is a plain dict update in precedence order.

    Args:
        table: a (possibly nested) mapping from tomllib.
        prefix: the dotted path so far.

    Returns:
        The flat mapping.
    """
    flat: dict[str, object] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, dotted + "."))
        else:
            flat[dotted] = value
    return flat


def _env_overrides(env: Mapping[str, str]) -> dict[str, object]:
    """Read KILN_SECTION__KEY variables (docs/spec/05 §1, step 3).

    Why:
        Environment variables are how tests, labs and containers change settings without a
        file. Values are parsed as TOML values, so "8" becomes 8 and "true" becomes True.

    Args:
        env: the environment, usually os.environ.

    Returns:
        Flat "section.key" → value pairs.

    Example:
        >>> _env_overrides({"KILN_MASTER__WORKERS": "8", "HOME": "/x"})
        {'master.workers': 8}
    """
    flat: dict[str, object] = {}
    for name, raw in env.items():
        if not name.startswith(ENV_PREFIX) or "__" not in name:
            continue
        dotted = name.removeprefix(ENV_PREFIX).lower().replace("__", ".")
        try:
            # WHY: parse "value = <raw>" as a TOML line to get TOML's types; if that fails
            # (for example a bare word like INFO), keep the raw string.
            flat[dotted] = tomllib.loads(f"value = {raw}")["value"]
        except tomllib.TOMLDecodeError:
            flat[dotted] = raw
    return flat


def _check_value(key: str, rule: Rule, value: object) -> tuple[object, str | None]:
    """Check one value against its rule, converting ints to floats where a float is expected.

    Why:
        One function knows every type rule, including the bool-is-an-int trap.

    Args:
        key: the "section.key" name (for messages).
        rule: its rule.
        value: the merged value.

    Returns:
        (clean value, None) if valid, or (original value, problem text).
    """
    is_bool = isinstance(value, bool)
    if rule.kind == "bool":
        if not is_bool:
            return value, f"{key} must be true or false (got {value!r})"
        return value, None
    if rule.kind == "list":
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            return value, f"{key} must be a list of strings (got {value!r})"
        return tuple(value), None
    if rule.kind == "str":
        if not isinstance(value, str):
            return value, f"{key} must be a string (got {value!r})"
        if rule.choices and value not in rule.choices:
            return value, f"{key} must be one of {', '.join(rule.choices)} (got {value!r})"
        return value, None
    if is_bool or not isinstance(value, int | float):
        return value, f"{key} must be a number (got {value!r})"
    if rule.kind == "int" and not isinstance(value, int):
        return value, f"{key} must be a whole number (got {value!r})"
    if rule.minimum is not None and value < rule.minimum:
        return value, f"{key} must be ≥ {rule.minimum:g} (got {value!r})"
    if rule.strictly_positive and value <= 0:
        return value, f"{key} must be > 0 (got {value!r})"
    return (float(value) if rule.kind == "float" else value), None


def _check_cross_field(values: dict[str, object]) -> list[str]:
    """Rules that involve two settings at once.

    Why:
        Some mistakes are only visible together, such as a heartbeat timeout shorter than
        the interval (every worker would be killed as "hung").

    Args:
        values: the cleaned flat values.

    Returns:
        Problems found.
    """
    problems = []
    interval = values["worker.heartbeat_interval_s"]
    timeout = values["worker.heartbeat_timeout_s"]
    if isinstance(interval, float) and isinstance(timeout, float) and timeout <= interval:
        problems.append(
            "worker.heartbeat_timeout_s must be larger than worker.heartbeat_interval_s "
            "(otherwise every worker looks hung)"
        )
    for key in ("master.bind", "gateway.bind"):
        bind = str(values[key])
        host, _, port = bind.rpartition(":")
        if not host or not port.isdigit() or not 0 <= int(port) <= 65535:
            problems.append(f"{key} must look like 127.0.0.1:7340 (got {bind!r})")
        elif host not in ("127.0.0.1", "localhost") and not values["master.allow_non_loopback"]:
            problems.append(
                f"{key} binds to {host}, which is reachable from other machines; Kiln has no "
                "authentication (ADR-021). Use 127.0.0.1, or set master.allow_non_loopback = true"
            )
    return problems


def build_config(flat: Mapping[str, object]) -> Config:
    """Validate flat "section.key" values (missing keys get defaults) and build the Config.

    Why:
        The single place where values become a Config; used by load_config and from_json.

    Args:
        flat: merged settings.

    Returns:
        The validated Config.

    Raises:
        ConfigError: listing every unknown key and every invalid value.
    """
    problems = [
        f"unknown setting {key!r} (check the spelling against the list in "
        "docs/spec/05-configuration-and-cli.md §2)"
        for key in flat
        if key not in RULES
    ]
    values: dict[str, object] = {}
    for key, rule in RULES.items():
        value, problem = _check_value(key, rule, flat.get(key, rule.default))
        values[key] = value
        if problem:
            problems.append(problem)
    if not problems:
        problems += _check_cross_field(values)
    if problems:
        raise ConfigError(problems)
    return _assemble(values)


def _section(values: Mapping[str, object], name: str) -> dict[str, object]:
    """Collect one section's values, keyed by dataclass field name.

    Why:
        The flat "section.key" table must be split back into one dict per dataclass before the
        dataclasses can be built.

    Args:
        values: cleaned flat values.
        name: the section, such as "worker".

    Returns:
        {"field": value, ...} for that section ("gateway.watchdog.x" becomes "watchdog_x").
    """
    prefix = name + "."
    return {
        key.removeprefix(prefix).replace(".", "_"): value
        for key, value in values.items()
        if key.startswith(prefix)
    }


def _assemble(values: Mapping[str, object]) -> Config:
    """Build the nested dataclasses from validated values.

    Why:
        Turns validated flat values into the nested, frozen Config object the rest of Kiln uses.

    Args:
        values: cleaned flat values (every key present, every type right).

    Returns:
        The Config.
    """
    # PY: **dict passes a dict's items as keyword arguments: Rule(**{"kind": "int"}) is
    # Rule(kind="int"). See docs/python/functions.md.
    # WHY (type: ignore[arg-type] below): every value was already checked against RULES, but
    # mypy can't know that a dict[str, object] holds exactly the right type for each field.
    return Config(
        master=MasterConfig(**_section(values, "master")),  # type: ignore[arg-type]
        app=AppConfig(**_section(values, "app")),  # type: ignore[arg-type]
        start=StartConfig(**_section(values, "start")),  # type: ignore[arg-type]
        worker=WorkerConfig(**_section(values, "worker")),  # type: ignore[arg-type]
        queue=QueueConfig(**_section(values, "queue")),  # type: ignore[arg-type]
        jobs=JobsConfig(**_section(values, "jobs")),  # type: ignore[arg-type]
        protocol=ProtocolConfig(**_section(values, "protocol")),  # type: ignore[arg-type]
        gateway=GatewayConfig(**_section(values, "gateway")),  # type: ignore[arg-type]
        debug=DebugConfig(**_section(values, "debug")),  # type: ignore[arg-type]
        labs=LabsConfig(**_section(values, "labs")),  # type: ignore[arg-type]
        log=LogConfig(**_section(values, "log")),  # type: ignore[arg-type]
    )


def load_config(
    path: Path | None,
    env: Mapping[str, str],
    overrides: Mapping[str, object] | None = None,
) -> Config:
    """Merge defaults < file < environment < overrides, validate, and return the Config.

    Why:
        The precedence of docs/spec/05 §1, in one function the CLI calls once.

    Args:
        path: a kiln.toml to read, or None (then ./kiln.toml is used if it exists).
        env: environment variables (KILN_SECTION__KEY ones are used).
        overrides: "section.key" → value from command-line flags.

    Returns:
        The validated Config.

    Raises:
        ConfigError: the file is unreadable or invalid TOML, or any value is invalid.
    """
    merged: dict[str, object] = {}
    file_path = path if path is not None else Path("kiln.toml")
    if path is not None or file_path.exists():
        try:
            merged.update(_flatten(tomllib.loads(file_path.read_text(encoding="utf-8"))))
        except OSError as error:
            raise ConfigError([f"cannot read config file {file_path}: {error.strerror}"]) from error
        except tomllib.TOMLDecodeError as error:
            raise ConfigError([f"{file_path} is not valid TOML: {error}"]) from error
    merged.update(_env_overrides(env))
    merged.update(overrides or {})
    return build_config(merged)


def default_config() -> Config:
    """Return the all-defaults Config (handy in tests and the REPL).

    Returns:
        A Config with every setting at its default.
    """
    return build_config({})
