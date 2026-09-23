"""IoU tracker with identity voting. Small, deterministic, tested."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from robot.perception.base import FaceDetection, iou


@dataclass
class Track:
    track_id: int
    det: FaceDetection
    first_seen: float
    last_seen: float
    votes: Counter[str] = field(default_factory=Counter)
    best_scores: dict[str, float] = field(default_factory=dict)
    name: str | None = None
    match_score: float | None = None
    announced_appeared: bool = False
    announced_name: str | None = None
    embed_attempts: int = 0

    def vote(self, name: str | None, score: float, needed: int) -> None:
        self.embed_attempts += 1
        if name is None:
            self.votes["?"] += 1
            return
        self.votes[name] += 1
        self.best_scores[name] = max(self.best_scores.get(name, 0.0), score)
        if self.votes[name] >= needed and self.name != name:
            self.name = name
            self.match_score = self.best_scores[name]

    def wants_embedding(self, every_n: int = 15) -> bool:
        """Embed until named; afterwards re-check occasionally to catch swaps."""
        if self.name is None:
            return True
        return self.embed_attempts % every_n == 0


class Tracker:
    def __init__(self, *, iou_threshold: float = 0.3, lost_after_s: float = 1.5, votes_needed: int = 3) -> None:
        self.iou_threshold = iou_threshold
        self.lost_after_s = lost_after_s
        self.votes_needed = votes_needed
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, dets: list[FaceDetection], now: float) -> tuple[list[Track], list[Track]]:
        """Associate detections; returns (matched_or_new tracks, tracks lost this call)."""
        unmatched = list(dets)
        assigned: list[Track] = []
        # greedy by IoU
        pairs: list[tuple[float, int, int]] = []
        track_list = list(self.tracks.values())
        for ti, tr in enumerate(track_list):
            for di, d in enumerate(unmatched):
                v = iou(tr.det.bbox, d.bbox)
                if v >= self.iou_threshold:
                    pairs.append((v, ti, di))
        pairs.sort(reverse=True)
        used_t: set[int] = set()
        used_d: set[int] = set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr = track_list[ti]
            tr.det = unmatched[di]
            tr.last_seen = now
            assigned.append(tr)
        for di, d in enumerate(unmatched):
            if di in used_d:
                continue
            tr = Track(self._next_id, d, now, now)
            self._next_id += 1
            self.tracks[tr.track_id] = tr
            assigned.append(tr)
        lost = [t for t in self.tracks.values() if now - t.last_seen > self.lost_after_s]
        for t in lost:
            del self.tracks[t.track_id]
        return assigned, lost

    def active(self) -> list[Track]:
        return sorted(self.tracks.values(), key=lambda t: t.track_id)
