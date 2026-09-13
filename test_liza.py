"""Offline smoke tests that never write to the configured production database."""


def test_security():
    from security import allow
    assert allow("isolated-test-key", 2, 60)
    assert allow("isolated-test-key", 2, 60)
    assert not allow("isolated-test-key", 2, 60)


def run():
    test_security()
    print("offline smoke tests: OK")


if __name__ == "__main__":
    run()
