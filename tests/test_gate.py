import json

from triage.evals.gate import check, read_metric


def test_read_metric_follows_the_dotted_path():
    assert read_metric({"metrics": {"all_ok": 0.93}}, "metrics.all_ok") == 0.93


def test_check_passes_at_and_above_the_threshold(tmp_path):
    path = tmp_path / "run.summary.json"
    path.write_text(json.dumps({"accuracy": 0.90}))
    assert check(path, "accuracy", 0.90)
    assert not check(path, "accuracy", 0.95)
