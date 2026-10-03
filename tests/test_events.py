from cam_recorder.events import Closed, EventTracker, Opened, State

POST, MAX = 60, 600
T = "binary_sensor.kamera_vorzimmer_motion_alarm"


def pulse(tr, t):
    """Tapo ONVIF: on and off within the same second."""
    return tr.trigger(T, True, t) + tr.trigger(T, False, t + 0.5)


def test_pulse_opens_event_and_closes_after_post():
    tr = EventTracker("vorzimmer", POST, MAX)
    acts = pulse(tr, 100)
    assert [type(a) for a in acts] == [Opened]
    ev = acts[0].event
    assert ev.start == 100 and tr.state is State.ACTIVE
    assert tr.tick(159) == []
    closed = tr.tick(160)
    assert [type(a) for a in closed] == [Closed]
    assert ev.reason == "quiet" and ev.end == 160 and tr.state is State.IDLE


def test_new_pulse_extends_open_event():
    tr = EventTracker("vorzimmer", POST, MAX)
    ev = pulse(tr, 100)[0].event
    assert pulse(tr, 150) == []  # no second event
    assert tr.tick(200) == []
    assert tr.tick(210)[0].event is ev
    assert ev.end == 210


def test_level_trigger_is_capped_and_needs_quiet_before_next_event():
    tr = EventTracker("g48", POST, MAX)
    ev = tr.trigger("binary_sensor.tor", True, 0)[0].event
    for t in range(1, 600):
        assert tr.tick(t) == []
    closed = tr.tick(600)
    assert closed[0].event is ev and ev.reason == "cap" and ev.end == 600
    assert tr.state is State.CAPPED
    # still on: no new event
    assert tr.tick(1000) == []
    tr.trigger("binary_sensor.tor", False, 1000)
    assert tr.tick(1059) == [] and tr.state is State.CAPPED
    tr.tick(1060)
    assert tr.state is State.IDLE
    assert isinstance(tr.trigger("binary_sensor.tor", True, 1100)[0], Opened)


def test_pulses_during_cap_keep_it_capped():
    tr = EventTracker("vorzimmer", POST, MAX)
    pulse(tr, 0)
    for t in range(30, 700, 30):
        pulse(tr, t)
        tr.tick(t)
    assert tr.state is State.CAPPED
    assert pulse(tr, 720) == []  # still within post of last pulse
    tr.tick(800)
    assert tr.state is State.IDLE


def test_quiet_close_wins_when_end_before_cap():
    tr = EventTracker("vorzimmer", POST, MAX)
    ev = pulse(tr, 0)[0].event
    pulse(tr, 500)
    acts = tr.tick(600)  # last pulse 500 -> end 560, before the cap at 600
    assert acts[0].event is ev and ev.reason == "quiet" and ev.end == 560


def test_suppress_closes_open_event_and_ignores_triggers():
    tr = EventTracker("wohnzimmer", POST, MAX)
    ev = pulse(tr, 0)[0].event
    acts = tr.suppress(True, 10)
    assert acts[0].event is ev and ev.reason == "suppressed" and ev.end == 10
    assert pulse(tr, 20) == [] and tr.tick(500) == []
    tr.suppress(False, 600)
    assert tr.state is State.IDLE
    assert isinstance(pulse(tr, 601)[0], Opened)


def test_trigger_on_through_privacy_does_not_open_on_release():
    tr = EventTracker("g48", POST, MAX)
    tr.suppress(True, 0)
    tr.trigger("binary_sensor.tor", True, 5)
    tr.suppress(False, 100)
    assert tr.state is State.CAPPED and tr.tick(101) == []
