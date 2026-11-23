"""Exception hierarchy.

Messages of :class:`DepaError` subclasses are written by this package and are safe to surface in
``result.json``: they never contain data values. Exceptions raised by third-party code may embed
data (for example a value that failed to parse), so the executor reports only their type.
"""


class DepaError(Exception):
    """Base class. The message must never contain data values."""


class RecipeError(DepaError):
    """The recipe (or a contract it is checked against) is invalid. Raised before data is touched."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid recipe")


class SafetyError(DepaError):
    """A safety rule was violated (disallowed class, SQL construct, file format, op, ...)."""


class StepError(DepaError):
    """A step failed at run time for a reason this package detected."""


class PrivacyError(DepaError):
    """Differential-privacy configuration or accounting failed a check."""
