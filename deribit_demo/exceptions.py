class BotError(Exception):
    """Base exception for the Deribit demo bot."""


class ConfigurationError(BotError):
    """Raised for invalid or incomplete local configuration."""


class AuthenticationError(BotError):
    """Raised when a private API request lacks valid credentials."""


class ExchangeError(BotError):
    """Raised when Deribit returns an API error."""


class TransientExchangeError(BotError):
    """Raised for retryable network or server-side failures."""

