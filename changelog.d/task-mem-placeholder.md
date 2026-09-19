### fix: `fleet-agent memory write` drops the seeded "(no entries yet …)" placeholder

The first `fleet-agent memory write` on a fresh store appended the real index
line but left the seeded `- *(no entries yet — …)*` placeholder in `MEMORY.md`.
`_update_index` now removes that placeholder line (matched by pattern, so both
the project and the global leader-memory templates are covered) whenever it adds
or updates an entry. All other lines of the index are left untouched (#269).
