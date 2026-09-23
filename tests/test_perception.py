from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from robot.core import paths
from robot.memory import Memory
from robot.perception.align import ARCFACE_TEMPLATE, align_face, similarity_transform
from robot.perception.base import FaceDetection, iou, nms
from robot.perception.matcher import IdentityIndex
from robot.perception.scrfd import decode_scrfd, encode_scrfd_for_test
from robot.perception.tracker import Tracker


def test_iou_and_nms():
    a = (0, 0, 10, 10)
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, (10, 10, 20, 20)) == 0.0
    assert iou(a, (5, 0, 15, 10)) == pytest.approx(1 / 3)
    dets = [FaceDetection(a, 0.9), FaceDetection((1, 1, 11, 11), 0.8), FaceDetection((50, 50, 60, 60), 0.7)]
    kept = nms(dets, 0.4)
    assert [d.score for d in kept] == [0.9, 0.7]


def test_scrfd_decode_round_trip():
    lm = np.array([[100, 90], [140, 90], [120, 110], [105, 130], [135, 130]], dtype=np.float32)
    faces = [
        ((90.0, 70.0, 150.0, 150.0), 0.9, lm),
        ((400.0, 300.0, 600.0, 520.0), 0.8, None),
        ((10.0, 10.0, 40.0, 40.0), 0.2, None),
    ]
    outputs = encode_scrfd_for_test(faces, (640, 640))
    dets = decode_scrfd(outputs, (640, 640), score_threshold=0.5)
    assert len(dets) == 2
    dets.sort(key=lambda d: d.score, reverse=True)
    assert dets[0].bbox == pytest.approx((90, 70, 150, 150), abs=1e-3)
    assert dets[0].landmarks is not None
    assert np.allclose(dets[0].landmarks, lm, atol=1e-3)
    assert dets[1].bbox == pytest.approx((400, 300, 600, 520), abs=1e-3)


def test_scrfd_decode_accepts_batched_and_logit_outputs():
    faces = [((90.0, 70.0, 150.0, 150.0), 0.9, None)]
    outputs = encode_scrfd_for_test(faces, (320, 320))
    batched = {k: v[None] for k, v in outputs.items()}
    assert len(decode_scrfd(batched, (320, 320))) == 1
    logits = dict(outputs)
    for k in list(logits):
        if k.startswith("score"):
            s = logits[k]
            logits[k] = np.where(s > 0, 3.0, -6.0).astype(np.float32)  # sigmoid(3)=0.95, sigmoid(-6)=0.002
    assert len(decode_scrfd(logits, (320, 320))) == 1


def test_alignment_identity_on_template():
    m = similarity_transform(ARCFACE_TEMPLATE)
    assert np.allclose(m, [[1, 0, 0], [0, 1, 0]], atol=1e-3)
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    img[50:150, 50:150] = 255
    shifted = ARCFACE_TEMPLATE + np.array([40.0, 30.0])
    out = align_face(img, shifted)
    assert out.shape == (112, 112, 3)
    assert out.sum() > 0


def test_tracker_association_votes_and_loss():
    tr = Tracker(iou_threshold=0.3, lost_after_s=1.0, votes_needed=2)
    d1 = FaceDetection((0, 0, 100, 100), 0.9)
    tracks, lost = tr.update([d1], now=0.0)
    assert len(tracks) == 1 and not lost
    t = tracks[0]
    tracks2, _ = tr.update([FaceDetection((5, 5, 105, 105), 0.9)], now=0.1)
    assert tracks2[0].track_id == t.track_id
    t.vote("ann", 0.7, 2)
    assert t.name is None
    t.vote("ann", 0.75, 2)
    assert t.name == "ann" and t.match_score == 0.75
    assert t.wants_embedding() is False
    # a new far-away face gets a new id; the old one is lost after the timeout
    tracks3, lost3 = tr.update([FaceDetection((500, 500, 600, 600), 0.9)], now=2.0)
    assert tracks3[0].track_id != t.track_id
    assert [x.track_id for x in lost3] == [t.track_id]


def test_memory_and_matcher(tmp_path: Path):
    mem = Memory(tmp_path / "m.sqlite")
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    mem.add_embedding("ann", a, "cpu")
    mem.add_embedding("ann", a * 3, "cpu")  # normalised on insert
    mem.add_embedding("bob", b, "cpu")
    mem.add_embedding("bob", b, "hailo")
    names, mat = mem.embeddings("cpu")
    assert names == ["ann", "ann", "bob"] and mat.shape == (3, 3)
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0)
    idx = IdentityIndex(mem, "cpu", threshold=0.5)
    assert idx.match(np.array([0.9, 0.1, 0.0]))[0] == "ann"
    name, score = idx.match(np.array([0.5, 0.5, 0.0]))
    assert name is not None and score == pytest.approx(0.7071, abs=1e-3)
    assert idx.match(np.array([0.0, 0.0, 1.0]))[0] is None
    # facts and forgetting
    mem.add_fact("likes tea", "ann")
    mem.add_fact("likes tea", "ann")
    assert mem.facts("ann") == ["likes tea"]
    mem.touch_seen("ann")
    person = mem.get_person("ann")
    assert person is not None and person.seen_count == 1
    assert mem.forget("ann") is True
    assert mem.embedding_count("ann") == 0 and mem.get_person("ann") is None
    idx.refresh(force=True)
    assert idx.size == 1
    assert mem.forget_all() == 1
    assert IdentityIndex(mem, "cpu", 0.5).match(a)[0] is None
    mem.log_event("test", "x")
    assert mem.recent_events(1)[0][1] == "test"
    mem.close()


def _models_present() -> bool:
    d = paths.models_dir()
    return (d / "face_detection_yunet_2023mar.onnx").exists() and (d / "face_recognition_sface_2021dec.onnx").exists()


SAMPLE = os.environ.get("ROBOT_TEST_PORTRAIT")


@pytest.mark.models
@pytest.mark.skipif(
    not _models_present() or not SAMPLE, reason="needs `robot provision --group vision-cpu` and ROBOT_TEST_PORTRAIT"
)
def test_cpu_backend_detects_and_matches_real_face(tmp_path: Path):
    import cv2

    from robot.perception.cpu import open_cpu_backend

    backend = open_cpu_backend()
    bgr = cv2.imread(str(SAMPLE))
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    dets = backend.detector.detect(rgb)
    assert len(dets) >= 1
    v1 = backend.embedder.embed(rgb, dets[0])
    assert v1.shape == (128,) and np.linalg.norm(v1) == pytest.approx(1.0, abs=1e-3)
    # same face, slightly rescaled: must match; the other people in the photo must not
    small = cv2.resize(rgb, None, fx=0.8, fy=0.8)
    d0 = dets[0]
    d2 = min(
        backend.detector.detect(small),
        key=lambda d: (d.center[0] - 0.8 * d0.center[0]) ** 2 + (d.center[1] - 0.8 * d0.center[1]) ** 2,
    )
    v2 = backend.embedder.embed(small, d2)
    assert float(v1 @ v2) > backend.embedder.default_threshold
    for other in dets[1:]:
        assert float(v2 @ backend.embedder.embed(rgb, other)) < backend.embedder.default_threshold
    mem = Memory(tmp_path / "m.sqlite")
    mem.add_embedding("person", v1, backend.name)
    name, _score = IdentityIndex(mem, backend.name, backend.embedder.default_threshold).match(v2)
    assert name == "person"
