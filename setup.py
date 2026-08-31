"""Backward-compatible build shim.

All project metadata now lives in ``pyproject.toml`` (PEP 621).  This file is
kept only so that legacy tooling invoking ``python setup.py ...`` still works.

Previously this file re-declared ``python_requires``, ``install_requires``,
``extras_require`` and ``packages``, which silently conflicted with
``pyproject.toml`` (e.g. it dropped the ``lightrag-hku<1.5`` pin and shipped the
legacy brand package).  Do not re-add metadata here — edit
``pyproject.toml`` instead.
"""

from setuptools import setup

if __name__ == "__main__":
    setup()
