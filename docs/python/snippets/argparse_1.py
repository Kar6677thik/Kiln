# A toy command-line tool shaped like `kiln`: a --version flag and subcommands.
import argparse
import sys


def build_parser():
    parser = argparse.ArgumentParser(prog="toy", description="A toy CLI shaped like kiln's.")
    parser.add_argument("--version", action="version", version="toy 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    greet = sub.add_parser("greet", help="say hello")
    greet.add_argument("name", help="who to greet")
    greet.add_argument("--times", type=int, default=1, help="how many times (default: 1)")

    sub.add_parser("doctor", help="pretend to check the environment")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    if args.command == "greet":
        for _ in range(args.times):
            print(f"hello, {args.name}")
    elif args.command == "doctor":
        print("all good")
    return 0


if __name__ == "__main__":
    # With no arguments at all, show the help (exit code 0), like many real tools do.
    sys.exit(main(sys.argv[1:] or ["--help"]))
