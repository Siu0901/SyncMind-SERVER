from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ParsedSection:
    text: str
    page_number: Optional[int] = None
    section: Optional[str] = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class ParsedDocument:
    sections: list[ParsedSection]