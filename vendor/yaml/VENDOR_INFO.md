# Vendored: PyYAML

- Upstream: https://github.com/yaml/pyyaml
- Version: 6.0.3
- Source: copied from `site-packages/yaml/` (pure-Python parts only)
- License: MIT (see `LICENSE` in this directory)
- Vendored: 2026-05-19

## Why vendored

agent-fleet ships with **zero third-party deps** so `git clone` is enough.
PyYAML is the only YAML library we use; including its pure-Python source
under `vendor/` keeps that promise while avoiding `pip install`.

## What was copied

Only the `.py` files under `site-packages/yaml/`. The `_yaml` C
extension was intentionally **not** included — its absence makes
`yaml.CSafeLoader` unavailable, but `yaml.safe_load` / `yaml.safe_dump`
fall back to the pure-Python implementation automatically.

## Updating

```
SRC=/path/to/pyyaml-X.Y.Z/lib/yaml
DST=$(git rev-parse --show-toplevel)/vendor/yaml
cp "$SRC"/*.py "$DST/"
# Update LICENSE if upstream license changes; bump the Version above.
```
