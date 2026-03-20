from dataclasses import dataclass, field


@dataclass
class WatchlistRecord:
    ids: list[str] = field(default_factory=list)

