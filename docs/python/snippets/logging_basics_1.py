# Configuring logging once, then logging from named loggers at different levels.
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kiln.demo")

log.debug("not shown: DEBUG is below the INFO threshold")
log.info("worker started")
log.warning("queue is %d%% full", 90)
log.error("worker %s died", 3)

quiet = logging.getLogger("kiln.quiet")
quiet.setLevel(logging.ERROR)  # this logger only lets ERROR and above through
quiet.warning("not shown: filtered by the logger's own level")
quiet.error("shown: ERROR passes the logger's level")

try:
    1 / 0
except ZeroDivisionError:
    log.exception("division failed")  # logs at ERROR and appends the traceback
