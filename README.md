# agent-fleet

Hierarchical multi-vendor agent orchestration over tmux.

`fleet` lets a human leader collaborate with one or more driver agents
(claude / codex) running in tmux panes, with team topology defined
per-project in YAML. Designed as the successor to `claude-forge`.

> Status: **WIP**. Bootstrapped 2026-05-19. Design doc: [docs/design.md](docs/design.md).

## Goals

1. The user talks to a **leader** for task assignment; talks to a **driver** directly when refining a task.
2. **Multi-vendor agents** (claude / codex) can be mixed in one project.
3. **Team topology** (solo / pair-review / multi-stage / race) is configurable per project via YAML.

## Non-goals

- Fully autonomous operation. Human intervention is the point.
- General-purpose agent orchestration. `fleet` core targets coding; other workflows are plugins.
- `pip install`. `git clone` and run `./fleet`.

## Quick start

```bash
git clone <this repo>
cd agent-fleet
./fleet init --name myproject /path/to/some/project
```

Requires Python 3.11+. No third-party dependencies (any needed libraries
are vendored under `vendor/`).

## Layout

```
agent-fleet/
  fleet           # CLI entrypoint (executable Python script)
  src/fleet/      # Python package (cli, state, commands, ...)
  tests/          # pytest tests
  docs/           # design documents
  vendor/         # vendored pure-Python deps (added as needed)
```

## License

TBD.
