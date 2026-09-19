### feat: add `fleet notify` to enable/disable the leader-pane push

New `fleet notify --project P [on|off|status]` sets `notify_leader_on_driver_done`
in `project.yaml` (the opt-in leader-pane push of driver `done` / approval gates,
design §10.3); no argument (or `status`) prints the current state. Previously no
command set the option, so a leader could not enable it without hand-editing state
files, which the leader protocol forbids. Written through `state.save_project`
(locked, atomic). Documented in README (en/ja) and the leader prompt's command list.
