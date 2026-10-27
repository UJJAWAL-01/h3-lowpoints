"""`python -m h3lowpoints`: the same as the `h3lowpoints` command."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
