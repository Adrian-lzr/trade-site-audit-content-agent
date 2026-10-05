from backend.services.deployment import (
    TargetObservation,
    callback_signature,
    configure_target_observer,
    fetch_target_observation,
    verify_callback_signature,
    verify_deployment,
)


def test_callback_signature_binds_attempt_revision_target_and_nonce():
    values = {
        "attempt_id": 7,
        "status": "deployed",
        "commit_sha": "a" * 40,
        "deployment_id": "fixture-1",
        "nonce": "nonce-1234",
        "revision_hash": "r" * 64,
        "target": "fixture",
    }
    signature = callback_signature(secret="secret", **values)
    assert verify_callback_signature(secret="secret", signature=signature, **values)
    assert not verify_callback_signature(secret="secret", signature=signature, **{**values, "nonce": "nonce-5678"})


def test_configured_fixture_observer_is_fresh_target_evidence():
    configure_target_observer(lambda target, *, attempt=None: TargetObservation(
        target=target,
        commit="a" * 40,
        revision_hash="r" * 64,
        content_hash="c" * 64,
        evidence={"source": "authorized-fixture", "synthetic": True},
    ))
    try:
        observation = fetch_target_observation("fixture")
        assert observation.target == "fixture"
        assert observation.evidence["synthetic"] is True
    finally:
        configure_target_observer(None)


def test_deployment_verification_requires_fresh_target_observation():
    result = verify_deployment(
        expected_commit="a" * 40,
        observed_commit=None,
        expected_revision_hash="r" * 64,
        observed_revision_hash=None,
        expected_content_hash="c" * 64,
        observed_content_hash=None,
    )
    assert result.status == "verification_unavailable"
    assert result.verified is False


def test_deployment_verification_detects_revision_or_content_mismatch():
    result = verify_deployment(
        expected_commit="a" * 40,
        observed_commit="a" * 40,
        expected_revision_hash="r" * 64,
        observed_revision_hash="s" * 64,
        expected_content_hash="c" * 64,
        observed_content_hash="d" * 64,
    )
    assert result.status == "mismatch"
    assert result.commit_matches is True
    assert result.revision_matches is False
    assert result.content_matches is False


def test_deployment_verification_only_marks_verified_when_all_bindings_match():
    result = verify_deployment(
        expected_commit="a" * 40,
        observed_commit="a" * 40,
        expected_revision_hash="r" * 64,
        observed_revision_hash="r" * 64,
        expected_content_hash="c" * 64,
        observed_content_hash="c" * 64,
    )
    assert result.status == "verified"
    assert result.verified is True
