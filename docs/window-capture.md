# Exact Windows window capture

The native plugin exposes a deliberately bounded Windows-only window-capture
surface. It does not forward arbitrary OBS input settings.

`create_window_capture_source` requires an exact scene name, source name,
process ID, window handle, and current window title. It also accepts one typed
capture method: `automatic`, `bitblt`, or `windows_graphics_capture`. The
native plugin then:

1. verifies that the HWND is live, visible, and owned by the requested PID;
2. derives the current title, window class, executable basename, and process
   creation time through Win32 APIs;
3. creates one libobs `window_capture` source and scene item; and
4. revalidates the same process object and window identity before returning a
   typed readback.

`get_window_capture_source` repeats the exact binding check without mutating
OBS. Call it immediately before recording. A missing window, PID/HWND reuse,
title drift, changed source settings, duplicate scene item, or changed process
object fails closed with a stable public error code.

Metadata readback cannot prove that OBS is rendering useful pixels. Call
`capture_program_frame` after binding validation and before every long
recording. It returns the current program scene as a fixed 320x180 in-memory
PNG with byte length and SHA-256; it accepts no source name or filesystem path.
Inspect that frame and fail closed on black or incorrect content.

`set_window_capture_method` changes only the capture method on an existing
exactly bound source. The plugin verifies the scene item, private binding
metadata, live process object, HWND, title, class, executable, cursor setting,
client-area setting, visibility, and current source kind before mutation. It
then reads the source back; a failed postcondition rolls the method back.

OBS automatic mode chooses BitBlt for window classes outside its WGC allowlist.
Use explicit `windows_graphics_capture` when a live game window produces a
black or incorrect BitBlt frame. The public API maps the enum internally and
does not accept raw integer settings.

The source stores only the exact binding metadata needed for later readback.
It returns the executable basename, never the full executable path. The API
does not accept raw source kinds, raw settings, or raw vendor requests.

On macOS and Linux the native request returns `OBS_UNSUPPORTED_PLATFORM`.
Use other reviewed, platform-specific typed capture contracts when they are
added; UI automation remains a last-resort fallback.
