class PublishUnavailable(RuntimeError):
    """A marketplace cannot be published to automatically (disabled, unconfigured, or unsupported)."""
