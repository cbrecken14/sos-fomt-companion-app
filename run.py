"""Packaging entry point — PyInstaller needs a plain script, not a `python -m app.main` invocation
(app/main.py uses a package-relative import that only works when run as part of the `app` package).
"""
from app.main import main

if __name__ == "__main__":
    main()
