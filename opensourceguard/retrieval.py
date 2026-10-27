"""Small dependency-free hybrid code retriever used by the demo and tests."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class CodeDocument:
    path: str
    text: str
    start_line: int = 1
    symbol: str = "<module>"
    language: str = "text"
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def tokens(self) -> list[str]:
        return [x.lower() for x in TOKEN.findall(self.path + " " + self.symbol + " " + self.text)]


class HybridRetriever:
    """BM25-like lexical score plus symbol/path and directory cohesion boosts."""

    def __init__(self, documents: Iterable[CodeDocument] = ()) -> None:
        self.documents = list(documents)
        self._df = Counter()
        self._tf: list[Counter[str]] = []
        for doc in self.documents:
            tf = Counter(doc.tokens)
            self._tf.append(tf)
            for token in tf:
                self._df[token] += 1

    @classmethod
    def from_tree(cls, root: str | Path, extensions: set[str] | None = None) -> "HybridRetriever":
        extensions = extensions or {".py", ".js", ".ts", ".java", ".go", ".rs", ".md"}
        docs: list[CodeDocument] = []
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file() or path.suffix.lower() not in extensions or any(x in path.parts for x in {".git", ".venv", "node_modules"}):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            docs.append(CodeDocument(str(path.relative_to(root)), text, language=path.suffix.lstrip(".") or "text"))
        return cls(docs)

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        qtokens = [x.lower() for x in TOKEN.findall(query)]
        if not qtokens:
            return []
        n = max(1, len(self.documents))
        scores: list[float] = []
        for doc, tf in zip(self.documents, self._tf):
            score = 0.0
            for token in qtokens:
                if not tf[token]:
                    continue
                idf = math.log(1 + (n - self._df[token] + .5) / (self._df[token] + .5))
                score += idf * (tf[token] * 2.2) / (tf[token] + 1.4)
            hay = (doc.path + " " + doc.symbol).lower()
            score += sum(1.2 for token in qtokens if token in hay)
            # Documentation is useful context, but executable code should win
            # when an Issue names a symbol. This keeps evidence actionable.
            if doc.language in {"md", "markdown", "txt"}:
                score *= 0.55
            if "/" in doc.path and any(part in doc.path.lower() for part in query.lower().split()):
                score += .25
            scores.append(score)
        directory_boost: defaultdict[str, float] = defaultdict(float)
        for doc, score in zip(self.documents, scores):
            if score > 0:
                directory_boost[str(Path(doc.path).parent)] += score * .08
        ranked = []
        for doc, score in zip(self.documents, scores):
            score += directory_boost[str(Path(doc.path).parent)]
            if score > 0:
                ranked.append({"path": doc.path, "start_line": doc.start_line, "end_line": doc.start_line + max(0, doc.text.count("\n")), "symbol": doc.symbol, "language": doc.language, "score": round(score, 4), "confidence": "retrieved"})
        ranked.sort(key=lambda x: (-x["score"], x["path"], x["start_line"]))
        return ranked[: max(1, min(top_k, 50))]
