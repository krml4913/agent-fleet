### feat: agent aliases — name a whole `vendor:model` spec and use the name anywhere a spec goes

A new `agent_aliases` map in `fleet-state/global/config.yaml`
(`fleet config set agent_aliases.<name> <vendor:model>`, plus `get` / the new
`unset`) names a full agent spec. The alias is accepted by formation `agent` /
`peer_review.agent`, `fleet-agent start --agent`, `fleet leader --agent` and
the `leader_agent` config key (`fleet config set leader_agent deep`; `fleet
config` shows `leader_agent: deep -> claude:opus`). It is resolved once
(`fleet.agents.resolve_spec`) where the spec enters task / leader state:
task.yaml, events, the dashboard, cost/usage and `session.json` carry the
resolved spec, with the alias name kept as `agent_alias`, so editing an alias
never changes a running task or leader. Alias names cannot contain `:`, targets
must be full specs (no alias chains), and an unknown alias is an error naming
the known aliases — including in `formation.validate`, which now also checks
stage `agent` / `peer_review.agent` specs. `fleet config unset <key>` also
clears a scalar key back to its default.
