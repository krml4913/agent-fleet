### chore: follow-up nits from the `merge` / `cleanup` leader-only guard review

Refs #188. Reformatted the collapsed multi-`with` statements in `tests/test_merge.py`,
documented `--allow-from-driver` on `merge` / `cleanup` in `README.md` / `README.ja.md`,
and made `deferred_launch.running_in_task_pane` and `task_context.in_driver_pane`
share one `FLEET_TASK_ID` reader (`task_context.driver_pane_task_id`). No behavior change.
