"""Domain exceptions for mapping to HTTP responses."""


class NotFoundError(Exception):
    """Resource not found."""


class ValidationError(Exception):
    """Validation failed."""


class IntegrityError(Exception):
    """Duplicate or constraint violation."""
