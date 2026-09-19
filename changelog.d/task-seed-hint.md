### feat: unseeded formation/role errors say how to seed; add `fleet formation seed` / `fleet role seed`

When `fleet-agent start` (or `fleet formation show`, the driver prompt render,
`fleet edit` validation) misses a formation or role that ships as a seed
(`src/fleet/templates/*.yaml`, `docs/prompts/roles/*.md`), the error now names the
shipped seed and the exact next step: the `fleet formation|role seed` command with
the resolved project/global target path, or `fleet edit`'s "seed shipped" mode.
A miss is still a hard error — shipped files are never a runtime fallback.

New non-interactive commands `fleet formation seed <name> [--global] [--project P]
[--force]` and `fleet role seed <name> [--global] [--project P] [--force]` copy a
shipped seed into the project (default) or global tier and refuse to overwrite an
existing file unless `--force`. Shared seed logic lives in `fleet.seeds`, which
`fleet edit` now also uses.
