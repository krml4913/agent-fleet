### change: teach driver prompts the user approval gate

The shared driver prompt now tells every driver to use `fleet-agent ask` instead
of the agent CLI's built-in interactive question tool, and documents that
`fleet-agent done` raises a `user_approval` gate without settling it. Drivers may
relay an in-pane user approval with `fleet-agent approve` only when the user gives
explicit approval of that finished deliverable; otherwise the leader/user settles
the gate.
