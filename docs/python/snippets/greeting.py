# A tiny module used by running-python-and-the-repl_1.py and the "python -m" example.
# It shows what the special variable __name__ contains in different situations.


def hello(name):
    return "hello, " + name


print("greeting.py is running; __name__ is", repr(__name__))

if __name__ == "__main__":
    # This block runs only when greeting.py is the program you started,
    # not when another file imports it.
    print(hello("script"))
