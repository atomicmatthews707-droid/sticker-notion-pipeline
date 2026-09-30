from dataclasses import dataclass
from typing import Optional

@dataclass
class NicheSignal:
    name: str
    source: str
    score: float
    metadata: Optional[dict] = None
