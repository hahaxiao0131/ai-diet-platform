from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Food


DEFAULT_RESULT_LIMIT = 10
SEARCH_RESULT_LIMIT = 16


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    return re.sub(r"[\s()（）·/\\_.，,、-]+", "", normalized)


def search_catalog(foods: list[Food], raw_query: str, limit: int | None = None) -> list[Food]:
    query = normalize_search_text(raw_query)
    if not query:
        return foods[: limit or DEFAULT_RESULT_LIMIT]

    scored: list[tuple[float, int, Food]] = []
    for index, food in enumerate(foods):
        score = _relevance(food, query)
        if score > 0:
            scored.append((score, index, food))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [food for _, _, food in scored[: limit or SEARCH_RESULT_LIMIT]]


def _relevance(food: Food, query: str) -> float:
    name = normalize_search_text(food.name)
    aliases = [normalize_search_text(alias) for alias in food.aliases]
    tags = [normalize_search_text(tag) for tag in food.tags]

    if query == name:
        return 100
    if query in aliases:
        return 96
    if name.startswith(query):
        return 90
    if any(alias.startswith(query) for alias in aliases):
        return 86
    if query in name:
        return 82
    if any(query in alias for alias in aliases):
        return 78
    if name in query or any(alias and alias in query for alias in aliases):
        return 72
    if query in tags:
        return 62
    if any(query in tag for tag in tags):
        return 55

    if len(query) >= 2:
        similarity = max(SequenceMatcher(None, query, candidate).ratio() for candidate in [name, *aliases] if candidate)
        if similarity >= 0.62:
            return 30 + similarity * 20
    return 0
