"""Ordering of a company's people: executives first, then the board, then advisors."""

import re

# (rank, pattern) - the lowest rank that matches the role wins, so "Chairman & CEO" counts as CEO.
RANKS = [
    (0, r"\bceo\b|chief executive|managing director|general manager|\bmd\b|chief exec"),
    (1, r"\bcfo\b|chief financial|finance director|head of finance"),
    (2, r"\bc[a-z]o\b|\bchief\b"),                       # COO, CTO, CMO, CSO, CRO, CIO... and any "Chief ..."
    (3, r"founder|\bpresident\b|\bvp\b|vice president|head of|\bdirector (of|for)\b|general counsel"),
    (4, r"chair(man|woman|person|men)?\b(?! of the audit)"),
    (5, r"board|non-?executive|\bndr\b|director|observer|trustee"),
    (6, r"advis|\bkol\b|mentor|consultant|scientific|clinical"),
]
COMPILED = [(rank, re.compile(pattern, re.I)) for rank, pattern in RANKS]
UNKNOWN = 7


def role_rank(role):
    role = role or ""
    ranks = [rank for rank, pattern in COMPILED if pattern.search(role)]
    return min(ranks) if ranks else UNKNOWN


def sort_people(rows, key=lambda row: row):
    """Stable order: by role rank, then name. ``key`` gives the Contact of a row."""
    return sorted(rows, key=lambda r: (role_rank(key(r).role), key(r).full_name.lower()))
