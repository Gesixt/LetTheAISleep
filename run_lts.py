"""Run the CLI from this clone without relying on an installed entry point."""
from lts.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
