from pathlib import Path

from cam_recorder import cleaner, storage
from cam_recorder.segments import Segment, day_dir, overlaps, parse_name, parse_segment_list

T0 = parse_name("20261003T200000Z.ts")


def seg(i, size=100, cam="vorzimmer", store="primary", dur=10):
    return Segment(cam, f"/r/{cam}/{i}.ts", T0 + i * dur, T0 + (i + 1) * dur, size, store)


def test_parse_name_is_utc():
    assert parse_name("20261003T200000Z.ts") == 1791057600.0
    assert day_dir("/r", "vorzimmer", T0) == Path("/r/vorzimmer/2026-10-03")


def test_parse_segment_list_skips_garbage():
    text = "20261003T200000Z.ts,0.000000,8.012000\nbroken\n20261003T200008Z.ts,8.012000,16.0\n"
    assert parse_segment_list(text) == [("20261003T200000Z.ts", 0.0, 8.012), ("20261003T200008Z.ts", 8.012, 16.0)]


def test_overlaps_half_open_and_open_end():
    assert overlaps(0, 10, 10, 20) is False
    assert overlaps(0, 10, 9.9, 20) is True
    assert overlaps(100, 110, 50, None) is True


def test_primary_below_limit_deletes_nothing():
    assert cleaner.plan_primary([seg(i) for i in range(10)], [], set(), 1000) == []


def test_primary_deletes_quiet_oldest_first_down_to_90_percent():
    segs = [seg(i) for i in range(20)]  # 2000 bytes, limit 1000 -> target 900
    windows = [(T0 + 0, T0 + 30)]  # segments 0..2 belong to an event
    out = cleaner.plan_primary(segs, windows, set(), 1000)
    names = [Path(s.path).stem for s in out]
    assert names[:3] == ["3", "4", "5"]
    assert "0" not in names and len(out) == 11


def test_primary_keeps_pending_and_eats_events_last():
    segs = [seg(i) for i in range(5)]  # 500, limit 100 -> target 90
    windows = [(T0, T0 + 20)]  # 0, 1 in event
    out = cleaner.plan_primary(segs, windows, {segs[0].path}, 100)
    assert [Path(s.path).stem for s in out] == ["2", "3", "4", "1"]


def test_fallback_keeps_recent_and_pending():
    segs = [seg(i, store="fallback") for i in range(30)]  # 300 s
    now = T0 + 300
    out = cleaner.plan_fallback(segs, {segs[0].path}, now, 120)
    assert segs[0] not in out
    assert max(s.end for s in out) < now - 120


def test_storage_needs_marker(tmp_path):
    primary, fb = tmp_path / "share", tmp_path / "tmp"
    primary.mkdir()
    assert storage.select(primary, fb).kind == "fallback"
    (primary / storage.MARKER).touch()
    st = storage.select(primary, fb)
    assert st.kind == "primary" and st.root == primary


MOUNTS = """overlay / overlay rw 0 0
/dev/sda8 /share ext4 rw 0 0
//192.168.1.20/cam-recorder /share/cam cifs rw 0 0
"""


def test_mount_fstype_longest_prefix():
    assert storage.mount_fstype("/share/cam/sub", MOUNTS) == "cifs"
    assert storage.mount_fstype("/share/cam-recorder", MOUNTS) == "ext4"
    assert storage.mount_fstype("/share", MOUNTS) == "ext4"


def test_ensure_marker_refuses_local_disk(tmp_path):
    mounts = f"/dev/sda8 {tmp_path} ext4 rw 0 0\n"
    assert storage.ensure_marker(tmp_path / "cam", mounts) is False
    assert not (tmp_path / "cam" / storage.MARKER).exists()


def test_ensure_marker_on_network_mount(tmp_path):
    mounts = f"//nas/x {tmp_path} cifs rw 0 0\n"
    assert storage.ensure_marker(tmp_path, mounts) is True
    assert (tmp_path / storage.MARKER).is_file()
