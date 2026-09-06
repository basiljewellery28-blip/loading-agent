"""test_collision_checker.py — Unit tests for Subagent Sentry (CollisionChecker)."""

from config.lp_config import LPConfig
from core.collision_checker import CollisionChecker, PlacedPartBox
from core.strategy_selector import BoundingBox


def test_clean_placement_passes_verification():
    """Test that well-spaced parts strictly inside the platform pass verification."""
    config = LPConfig()
    checker = CollisionChecker(config)

    # Two parts spaced 10mm apart
    p1 = PlacedPartBox("Ring1", BoundingBox(10.0, 30.0, 10.0, 30.0, 0.5, 10.5))
    p2 = PlacedPartBox("Ring2", BoundingBox(40.0, 60.0, 10.0, 30.0, 0.5, 10.5))

    report = checker.verify_placed_boxes([p1, p2])
    assert report.passed is True
    assert len(report.envelope_violations) == 0
    assert len(report.clearance_violations) == 0


def test_platform_envelope_violation_caught():
    """Test that any part exceeding platform boundaries is caught."""
    config = LPConfig(platform_x=235.0, platform_y=138.0, platform_z=100.0)
    checker = CollisionChecker(config)

    # Box protruding beyond platform_x (240 > 235)
    bad_box = PlacedPartBox("OversizedPart", BoundingBox(220.0, 240.0, 10.0, 30.0, 0.5, 10.5))

    report = checker.verify_placed_boxes([bad_box])
    assert report.passed is False
    assert len(report.envelope_violations) == 1
    assert "exceeds WaxJet 51C envelope" in report.envelope_violations[0]


def test_clearance_breach_caught():
    """Test that parts placed with insufficient clearance (< 1.5mm) trigger violation."""
    config = LPConfig(clearance_buffer=2.0)
    checker = CollisionChecker(config)

    # Box 1 ends at X=30.0, Box 2 starts at X=30.8 -> gap is 0.8mm (< 1.5mm)
    p1 = PlacedPartBox("Ring1", BoundingBox(10.0, 30.0, 10.0, 30.0, 0.5, 10.5))
    p2 = PlacedPartBox("Ring2", BoundingBox(30.8, 50.0, 10.0, 30.0, 0.5, 10.5))

    report = checker.verify_placed_boxes([p1, p2], min_clearance_threshold=1.5)
    assert report.passed is False
    assert len(report.clearance_violations) == 1
    assert report.has_hard_collisions is False
    assert report.clearance_violations[0].actual_distance_mm < 1.0


def test_hard_collision_detected():
    """Test that overlapping boxes trigger hard collision flag."""
    config = LPConfig()
    checker = CollisionChecker(config)

    # Box 1 and Box 2 overlap in all 3 dimensions
    p1 = PlacedPartBox("RingA", BoundingBox(10.0, 30.0, 10.0, 30.0, 0.5, 10.5))
    p2 = PlacedPartBox("RingB", BoundingBox(25.0, 45.0, 20.0, 35.0, 5.0, 15.0))

    report = checker.verify_placed_boxes([p1, p2])
    assert report.passed is False
    assert report.has_hard_collisions is True
    assert report.clearance_violations[0].is_hard_collision is True
