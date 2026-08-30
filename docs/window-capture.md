# Exact Windows window capture

The native plugin exposes a deliberately bounded Windows-only window-capture
surface. It does not forward arbitrary OBS input settings.

`create_window_capture_source` requires an exact scene name, source name,
process ID, window handle, and current window title. The native plugin then:

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

The source stores only the exact binding metadata needed for later readback.
It returns the executable basename, never the full executable path. The API
does not accept raw source kinds, raw settings, or raw vendor requests.

On macOS and Linux the native request returns `OBS_UNSUPPORTED_PLATFORM`.
Use other reviewed, platform-specific typed capture contracts when they are
added; UI automation remains a last-resort fallback.
