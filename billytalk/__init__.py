"""BillyTalk — Windows dictation app.

The package is split into two processes (ADR-0001): ``billytalk.core`` owns every
Windows API surface — hooks, audio, clipboard, insertion — and ``billytalk.ui``
owns the wxPython interface. They never share a process, so a UI crash cannot
take dictation down with it.
"""

__version__ = "0.1.0"
