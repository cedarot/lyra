class LyraError(Exception):
    """Base class for expected Lyra errors."""


class ConfigError(LyraError):
    pass


class NoResultsError(LyraError):
    pass


class SiteError(LyraError):
    def __init__(self, site_id: str, category: str, message: str):
        self.site_id = site_id
        self.category = category
        super().__init__(message)


class ResourceUnavailableError(LyraError):
    pass


class SelectionError(LyraError):
    pass
