### feat: native Windows desktop notifications

`fleet.notify` gains a Windows transport next to macOS and Slack: on Windows it
shows a WinRT toast (`ToastNotificationManager`) through
`powershell.exe -EncodedCommand`, under PowerShell's own AppUserModelID so no
registration is needed. Default-on; disable with `windows: {enabled: false}` in
`notify.yaml`, and `FLEET_NO_NOTIFY` suppresses it like the others. Title/body
are XML-escaped and truncated like macOS, the level emoji is included, and the
call is best-effort (5 s timeout, `CREATE_NO_WINDOW`, warns on stderr, never
raises). Other platforms never invoke it.
