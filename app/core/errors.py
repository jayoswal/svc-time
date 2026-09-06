from dataclasses import dataclass, field


@dataclass
class AppError(Exception):
    status_code: int
    code: str
    message: str
    details: list[dict[str, str]] = field(default_factory=list)
