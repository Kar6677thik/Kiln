# Building a small aligned table with f-strings, like `kiln doctor` prints.
checks = [
    ("D1", "pass", "running on Linux"),
    ("D9", "warn", "no C compiler (needed by K4)"),
    ("D16", "pass", "scripts use LF line endings"),
]

print(f"{'check':<6}{'status':<8}detail")
for check_id, status, detail in checks:
    print(f"{check_id:<6}{status:<8}{detail}")

passed = sum(1 for _, status, _ in checks if status == "pass")
print(f"{passed}/{len(checks)} passed ({passed / len(checks):.0%})")
