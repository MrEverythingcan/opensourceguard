"""OpenSourceGuard: an evidence-first open-source maintainer agent."""

__version__ = "0.6.0"

from .autonomy import AutonomyPolicy, AutonomousMaintainer, AutonomyStore
from .retrieval import CodeDocument, HybridRetriever

__all__ = [
    "__version__",
    "AutonomyPolicy",
    "AutonomousMaintainer",
    "AutonomyStore",
    "CodeDocument",
    "HybridRetriever",
]
