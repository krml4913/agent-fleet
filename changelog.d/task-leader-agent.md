### feat: `leader_agent` global config key sets the default `fleet leader` agent

`fleet leader` always launched with the hardcoded default `claude:opus`, so the
only way to change the leader's model persistently was typing `--agent` on
every launch. A new `leader_agent` key in `fleet-state/global/config.yaml`
(`fleet config set leader_agent <vendor:model>`) is validated with the same
`vendor:model` parser `--agent` uses (an unknown vendor is rejected with the
supported list) and is free-form rather than enumerable, unlike `mux`
(`fleet.config.FREEFORM` alongside the existing `fleet.config.KEYS`).
Precedence: `fleet leader --agent` > `leader_agent` in the config > the
built-in default `claude:opus`. `fleet config` / `get` show it with its
source, like every other key; a missing or malformed value warns and falls
back to the default, same as `mux`. `fleet leader --help` and the README
(en/ja) and design.md document the new key. Closes #305.
