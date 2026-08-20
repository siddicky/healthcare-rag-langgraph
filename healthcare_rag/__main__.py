"""
Main entry point for running healthcare-rag as a module.

Example usage:
    python -m healthcare_rag
"""

from dotenv import load_dotenv

# Must run before any model client is constructed: secrets live in .env.
load_dotenv()

from .cli.interactive import main  # noqa: E402 - env must be loaded first

if __name__ == "__main__":
    main()