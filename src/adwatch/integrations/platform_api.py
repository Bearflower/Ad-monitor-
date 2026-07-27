from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformApiStatus:
    platform: str
    status: str
    reason: str


@dataclass(frozen=True)
class UnconfiguredPlatformApi:
    platform: str

    def status(self) -> PlatformApiStatus:
        return PlatformApiStatus(
            platform=self.platform,
            status="pending_external",
            reason="Official API OAuth is not configured",
        )
