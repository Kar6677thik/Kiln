# Run with: python docs/python/snippets/running-python-and-the-repl_1.py
# Shows the difference between a file being *run* and a file being *imported*.
import greeting

print("this file's __name__ is", repr(__name__))
print(greeting.hello("importer"))
