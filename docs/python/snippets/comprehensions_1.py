# Comprehensions over a list of results, the way kiln doctor summarizes its checks.
results = [
    {"id": "D1", "status": "pass"},
    {"id": "D4", "status": "warn"},
    {"id": "D9", "status": "warn"},
    {"id": "D14", "status": "pass"},
]

not_passing = [r["id"] for r in results if r["status"] != "pass"]
by_id = {r["id"]: r["status"] for r in results}
statuses = {r["status"] for r in results}
any_fail = any(r["status"] == "fail" for r in results)

print("not passing:", not_passing)
print("lookup table:", by_id)
print("distinct statuses:", sorted(statuses))
print("any failure?", any_fail)

# When NOT to use one: if the body needs several steps, a plain loop is clearer.
messages = []
for r in results:
    if r["status"] == "warn":
        label = r["id"].lower()
        messages.append(f"{label}: needs attention later")
print(messages)
