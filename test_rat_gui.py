from rat_gui import TAP_REFRACTORY_S, is_event, move_started, parse_line

assert parse_line("36.50,1,0.123\r\n") == (36.5, 1, 0.123)
assert parse_line("garbage") is None
assert parse_line("") is None
assert is_event(1.0, 0.8, now=10, last=0)
assert not is_event(0.5, 0.8, now=10, last=0)
assert not is_event(1.0, 0.8, now=10.2, last=10)  # inside jump refractory
assert is_event(0.4, 0.3, now=10.3, last=10, refractory_s=TAP_REFRACTORY_S)
assert move_started(True, False, now=10, last_beep=0)
assert not move_started(True, True, now=10, last_beep=0)  # still moving: beep only once
assert not move_started(True, False, now=10.5, last_beep=10)  # flicker within 1 s: no beep
assert not move_started(False, True, now=10, last_beep=0)
print("ok")
