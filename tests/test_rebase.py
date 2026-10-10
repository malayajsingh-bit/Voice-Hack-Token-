import fix as fx


def test_two_fixes_stack():
    base = "a\nPRICE bad\nb\nJARGON bad\nc"
    f1 = "a\nPRICE good\nb\nJARGON bad\nc"
    f2 = "a\nPRICE bad\nb\nJARGON good\nc"
    live = fx.rebase(base, f1, base)
    assert fx.rebase(base, f2, live) == "a\nPRICE good\nb\nJARGON good\nc"
