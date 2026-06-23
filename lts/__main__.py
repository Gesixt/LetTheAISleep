"""Enable `python -m lts` as an invocation of the CLI."""

import sys

from lts.cli import main

if __name__ == "__main__":
    sys.exit(main())
