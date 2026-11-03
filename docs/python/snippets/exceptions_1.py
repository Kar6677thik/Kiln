# Run with: python docs/python/snippets/exceptions_1.py
# Prints a full traceback (without crashing) so you can learn to read one.
import traceback


def read_workers(config):
    return int(config["workers"])


def start(config):
    return read_workers(config)


try:
    start({"workers": "four"})
except ValueError:
    traceback.print_exc()
print("the program kept going after printing the traceback")
