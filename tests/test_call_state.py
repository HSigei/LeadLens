from app import requires_recording_consent


def test_recording_consent_defaults_to_required():
    assert requires_recording_consent({}) is True


def test_explicit_recording_consent_policy_is_respected():
    assert requires_recording_consent({"recording_consent_required": "false"}) is False