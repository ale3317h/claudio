from core.boot_anim import fire_once, render_frame


def test_frames_deterministic():
    assert render_frame(0).plain == render_frame(0).plain


def test_frame_has_rings_core_and_title():
    plain = render_frame(3).plain
    assert "J A R V I S" in plain
    assert any(c in plain for c in "-+:*#%=")


def test_frames_change_over_time():
    assert render_frame(0).plain != render_frame(6).plain


def test_ascii_only():
    plain = render_frame(4).plain
    assert all(ord(c) < 128 for c in plain)


def test_fire_once_line():
    assert "J A R V I S" in fire_once()
