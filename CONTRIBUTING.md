# Contributing to Echo

Thanks for contributing. By submitting a contribution, you agree that it is
licensed under the Apache License 2.0, consistent with this repository.

## Before opening a pull request

- Discuss substantial changes in an issue first.
- Keep changes focused and add or update tests for behavior changes.
- Do not include credentials, personal data, private workspaces, or generated
  artifacts such as `__pycache__` files.
- Preserve third-party notices and do not copy code unless its license is
  compatible and documented in `THIRD_PARTY_NOTICES.md`.

## Local validation

Run from the repository root:

```bash
PYTHONPATH=. python3 -m unittest discover -s test/agent -t . -p 'test_*.py'
PYTHONPATH=. python3 -m unittest discover -s test/chat -t . -p 'test_*.py'
PYTHONPATH=. python3 -m unittest discover -s test/scripts -t . -p 'test_*.py'
python3 -m compileall -q agent chat tools
python3 scripts/release_check.py
python3 scripts/build_package.py /tmp/echo.sublime-package
```

## Pull request expectations

Describe the user-visible behavior, validation performed, and any security or
compatibility impact. Maintainers may request a smaller change or a regression
test before merging.
