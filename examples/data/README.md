# examples/data — Sample Documents

This folder holds lightweight documents used by the **QueryNest** demo / tests.

## `sample.txt` (works out of the box)

`sample.txt` is a small pure-text document describing how QueryNest's hybrid
retrieval works. It can be ingested **without any heavy dependency** (no MinerU):

```bash
python examples/quickstart.py examples/data/sample.txt

# or, via the CLI:
python -m querynest.cli ingest examples/data/sample.txt
python -m querynest.cli query "QueryNest 的混合检索包含哪几条召回路径？"
```

## `sample.pdf` (add your own)

We do **not** commit a copyright-ambiguous large paper. If you want to demo a
PDF, drop your own file here and name it `sample.pdf`:

```bash
python examples/quickstart.py examples/data/sample.pdf "你的问题"
```

PDF parsing requires a heavy parser (default `mineru`). Install it first:

```bash
pip install -e ".[all]"        # or follow MinerU's own install guide
QUERYNEST_PARSER=mineru python examples/quickstart.py examples/data/sample.pdf "..."
```

> The `.txt` / `.md` route uses QueryNest's dependency-free `LiteTextParser` and
> is the recommended way to run a real end-to-end demo on a modest machine.