# SPDX-License-Identifier: MIT
"""One focused end-to-end assertion: malformed original bounty claim is a safe CLI error."""
import json

import pytest

from concierge.mova_factory import MovaFactoryError, compile_mova_packet, main
from tests.test_mova_factory import POLICY, candidate


def test_wrong_target_preserves_factory_and_cli_error_contract(tmp_path, capsys):
    packet = candidate()
    packet["compensation_claim"]["text"] = "/claim #43\nI request the $50 bounty payment."

    with pytest.raises(MovaFactoryError, match="does not match canonical target"):
        compile_mova_packet(packet, repo_eligibility_policy=POLICY)

    candidate_file = tmp_path / "candidate.json"
    policy_file = tmp_path / "policy.json"
    candidate_file.write_text(json.dumps(packet), encoding="utf-8")
    policy_file.write_text(json.dumps(POLICY), encoding="utf-8")
    assert main([str(candidate_file), "--repo-eligibility-policy", str(policy_file)]) == 2
    captured = capsys.readouterr()
    assert "mova-factory: invalid candidate" in captured.err
    assert not captured.out
