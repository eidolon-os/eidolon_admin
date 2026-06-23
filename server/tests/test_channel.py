from fastapi.testclient import TestClient


def test_channel_benchmark_api_removed(app):
    client = TestClient(app)

    resp = client.get("/api/channel/benchmarks/runs")

    assert resp.status_code == 404
