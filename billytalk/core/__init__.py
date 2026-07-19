"""Headless core process: hooks, audio, state machine, providers, insertion, store.

Boundary rule (harness §1): everything that touches Windows input, clipboard or
audio APIs lives here. ``billytalk.ui`` imports ``ctypes`` only for window styles.
"""
