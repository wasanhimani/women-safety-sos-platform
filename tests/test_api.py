def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ready_when_queue_reachable(client):
    assert client.get("/readyz").status_code == 200


def test_metrics_page(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "sos_alerts_received_total" in resp.text
