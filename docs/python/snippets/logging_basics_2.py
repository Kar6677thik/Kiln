# One log call, two destinations with different formats: what handlers are for.
# Kiln writes one event per line as key=value pairs (spec/06); this is a tiny preview.
import logging
import sys

log = logging.getLogger("kiln.master")
log.setLevel(logging.DEBUG)
log.propagate = False  # don't also pass records up to the root logger

human = logging.StreamHandler(sys.stdout)
human.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
human.setLevel(logging.INFO)

machine = logging.StreamHandler(sys.stdout)
machine.setFormatter(logging.Formatter("level=%(levelname)s logger=%(name)s event=%(message)s"))

log.addHandler(human)
log.addHandler(machine)

log.debug("worker_spawned")  # only the machine handler (no level of its own) shows DEBUG
log.info("worker_ready")  # both handlers show INFO
