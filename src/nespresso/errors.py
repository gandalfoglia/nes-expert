"""Errors a person can act on."""

from __future__ import annotations


class NespressoError(Exception):
    """Expected failure while talking to the machine."""


class MachineNotFound(NespressoError):
    """No usable machine was discovered or the saved one did not answer."""


class AuthError(NespressoError):
    """Pairing or the saved key was refused."""


class BrewRejected(NespressoError):
    """The machine answered a brew or stop with an error."""


FACTORY_RESET_STEPS = (
    "Turn the machine off, close the slider, and hold the brew button for 6 seconds. "
    "Choose Lungo and press brew. Turn the temperature dial to maximum and press brew again. "
    "All lights blink when the reset finishes."
)

ALREADY_PAIRED = (
    "This machine already has a pairing key, so it will not accept a new one. "
    f"Factory-reset it ({FACTORY_RESET_STEPS}) Then run `nespresso pair`. "
    "A reset also unpairs the Nespresso phone app."
)

UNKNOWN_KEY = (
    "This machine is already paired, and ~/.nespresso_auth.json has no key for it. "
    f"Factory-reset it ({FACTORY_RESET_STEPS}) Then run `nespresso pair`."
)

BIND_FAILED = (
    "Pairing did not stick: the machine never reported pair state 2. "
    "If the phone app still owns the machine, factory-reset it, then run `nespresso pair`."
)

NO_MACHINE = (
    "No Nespresso machine found. Turn it on, stand close, and quit the Nespresso app. "
    "The machine accepts one Bluetooth connection."
)
