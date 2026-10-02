from backend.services.deployment import verify_deployment


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
