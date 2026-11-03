# FAQ: "But why...?"

**Why build this instead of using Celery or gunicorn?**
Because the goal is to *understand* them. Each milestone ends with a "Reference comparison" in which we read how gunicorn, Celery, uvicorn, Memray and others solved the same problem, after building our own version.

**Why does Kiln run in WSL and not on Windows?**
Kiln depends on `fork()`, Unix signals, `/proc` and cgroups, and Windows has none of these. WSL2 runs a real Linux kernel. See [ADR-003](decisions.md#adr-003).

**Why does the master never use threads?**
The master calls `fork()`. A fork copies only the thread that called it. Any lock that another thread held at that instant stays locked forever in the child. See [ADR-006](decisions.md#adr-006).

**Why JSON and not pickle for job arguments?**
`pickle` can run arbitrary code when loading, and it ties clients to the server's Python version. See [ADR-009](decisions.md#adr-009).

**If a worker dies, is my job lost?**
By default, yes: it becomes `LOST`. Kiln can't know whether your function already did its side effect, such as sending an email. Opt into retries only for idempotent tasks. See [spec/03 §5](spec/03-jobs-and-tasks.md#5-delivery-semantics-how-many-times-can-my-function-run).

**Why does the memory of all workers add up to more than my RAM?**
RSS counts shared copy-on-write pages once in *every* process. PSS divides them fairly. This is the subject of K3.

**Why are there so many comments in the code?**
It's a learning repo. See [standards/documentation.md §4.4](standards/documentation.md#44-how-dense-should-comments-be).
