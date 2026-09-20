### fix: `fleet attach` waits for Enter after the "another client is attached" note (zellij)

When another client is already attached to the session, `ZellijMux.attach` cannot focus
the task tab and prints "press Ctrl t, then N" instead — but `zellij attach` took over
the screen immediately, so the note flashed by unread and only surfaced in the scrollback
after detaching. When both stdin and stderr are a TTY it now prints `press Enter to
attach…` and waits before handing the terminal over (EOF on stdin just continues).
Without a TTY (piped runs, CI, tests) nothing is read and behaviour is unchanged, and the
no-other-client path (background focus helper) is untouched. Closes #297.
