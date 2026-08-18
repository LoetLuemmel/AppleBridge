"""Marks `tests/` as a REGULAR package, which is load-bearing.

`test_lint_arm.py` does `from tests.test_build_verification import ...`. Without
this file `tests/` is only a *namespace* portion, and Python's import machinery
keeps scanning the rest of `sys.path` after finding one — a regular package
found later wins, whatever the path order says.

That is not hypothetical: a developer Mac carries an unrelated
`site-packages/tests/__init__.py` (pulled in by some library), so the import
resolved to *that* package and the suite failed with
`ModuleNotFoundError: No module named 'tests.test_build_verification'` — while
CI's clean image passed, because nothing there had polluted site-packages.
A suite whose result depends on which other software the machine happens to
have installed is not reporting on this repository.

Deliberately empty otherwise: the tests keep their own `sys.path` handling.
"""
