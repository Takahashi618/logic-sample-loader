from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
__all__ = ['KIND_ONESHOT', 'KIND_LOOP', 'KIND_FX', 'KIND_LABELS', 'DEFAULT_ONESHOT_MAX_SEC', 'PACK_ORDER_NAME', 'PACK_ORDER_COUNT', 'PACK_ORDER_MTIME', 'PACK_ORDER_ADDED', 'PACK_ORDER_OPTIONS', 'PackStat', 'kind_from_duration', 'resolve_kind', 'pack_stats', 'sort_packs', 'next_pack_order']
KIND_ONESHOT = 'one_shot'
KIND_LOOP = 'loop'
KIND_FX = 'fx'
KIND_LABELS: dict[str, str] = {KIND_ONESHOT: 'ワンショット', KIND_LOOP: 'ループ', KIND_FX: '効果音', '': '不明'}
DEFAULT_ONESHOT_MAX_SEC: float = 2.0
PACK_ORDER_NAME = 'name'
PACK_ORDER_COUNT = 'count'
PACK_ORDER_MTIME = 'mtime'
PACK_ORDER_ADDED = 'added'
PACK_ORDER_OPTIONS: list[tuple[str, str]] = [(PACK_ORDER_COUNT, '中身の多い順'), (PACK_ORDER_NAME, '名前順'), (PACK_ORDER_MTIME, '更新の新しい順'), (PACK_ORDER_ADDED, '追加した順。古いものから')]

def kind_from_duration(duration: float | None, oneshot_max_sec: float=DEFAULT_ONESHOT_MAX_SEC) -> str | None:
    if duration is None or duration <= 0:
        return None
    return KIND_ONESHOT if duration <= oneshot_max_sec else KIND_LOOP

def resolve_kind(name_kind: str | None, duration: float | None, oneshot_max_sec: float=DEFAULT_ONESHOT_MAX_SEC) -> str:
    named = (name_kind or '').strip()
    if named == KIND_FX:
        return KIND_FX
    by_duration = kind_from_duration(duration, oneshot_max_sec)
    if by_duration == KIND_LOOP:
        return KIND_LOOP
    if named:
        return named
    if by_duration is not None:
        return by_duration
    return ''

@dataclass(frozen=True)
class PackStat:
    count: int = 0
    mtime: float = 0.0
    added: float = 0.0

def pack_stats(entries: Sequence[Any]) -> dict[str, PackStat]:
    counts: dict[str, int] = {}
    newest: dict[str, float] = {}
    oldest: dict[str, float] = {}
    for entry in entries:
        name = (getattr(entry, 'pack', '') or '').strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        try:
            stamp = float(getattr(entry, 'mtime', 0.0) or 0.0)
        except (TypeError, ValueError):
            stamp = 0.0
        if stamp > newest.get(name, 0.0):
            newest[name] = stamp
        if stamp > 0 and (name not in oldest or stamp < oldest[name]):
            oldest[name] = stamp
    return {name: PackStat(count=count, mtime=newest.get(name, 0.0), added=oldest.get(name, 0.0)) for name, count in counts.items()}

def sort_packs(stats: dict[str, PackStat], order: str=PACK_ORDER_COUNT) -> list[str]:
    if order == PACK_ORDER_NAME:
        return sorted(stats, key=str.lower)
    if order == PACK_ORDER_MTIME:
        return sorted(stats, key=lambda n: (-stats[n].mtime, n.lower()))
    if order == PACK_ORDER_ADDED:
        return sorted(stats, key=lambda n: (stats[n].added <= 0, stats[n].added, n.lower()))
    return sorted(stats, key=lambda n: (-stats[n].count, n.lower()))

def next_pack_order(order: str) -> str:
    values = [value for value, _ in PACK_ORDER_OPTIONS]
    if order not in values:
        return values[0]
    return values[(values.index(order) + 1) % len(values)]
