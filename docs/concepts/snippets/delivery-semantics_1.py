"""Why retrying after a crash can run a side effect twice (at-least-once)."""

emails_sent = []


def send_email(to):
    emails_sent.append(to)  # the side effect happens...
    raise ConnectionResetError("worker died before reporting success")  # ...then the report is lost


def run_with_retries(task, max_attempts):
    for attempt in range(1, max_attempts + 1):
        try:
            task()
            return f"succeeded on attempt {attempt}"
        except ConnectionResetError:
            print(f"attempt {attempt}: result lost")
    return "gave up"


print("max_attempts=1:", run_with_retries(lambda: send_email("a@example.com"), 1))
print("emails actually sent:", emails_sent)
emails_sent.clear()
print("max_attempts=3:", run_with_retries(lambda: send_email("a@example.com"), 3))
print("emails actually sent:", emails_sent)

# An idempotent version: remember what was already done, keyed by a unique job id.
done = set()


def send_once(job_id, to):
    if job_id in done:
        return "already sent"
    done.add(job_id)
    return f"sent to {to}"


print(send_once("job-42", "a@example.com"))
print(send_once("job-42", "a@example.com"))
