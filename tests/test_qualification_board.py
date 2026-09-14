import json
import threading
import urllib.request
import http.client

from scripts.qualification_board import TOKEN, QualificationBoard, server


def _request(root, path, *, method="GET", body=None):
    raw = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(root + path, data=raw, method=method, headers={"Authorization": f"Token {TOKEN}"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def test_mutable_qualification_board_exposes_real_submission_and_instance_effects():
    board = QualificationBoard()
    endpoint = server(board)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{endpoint.server_port}"
    try:
        assert _request(root, "/api/v1/challenges")["data"][0]["id"] == 1
        assert _request(root, "/api/v1/challenges/1")["data"]["type"] == "standard"
        submitted = _request(
            root,
            "/api/v1/challenges/attempt",
            method="POST",
            body={"challenge_id": 1, "submission": "qualification{accepted}"},
        )
        assert submitted["data"]["status"] == "correct"
        try:
            _request(
                root,
                "/api/v1/challenges/attempt",
                method="POST",
                body={"challenge_id": 1, "submission": "qualification{ambiguous}"},
            )
        except http.client.RemoteDisconnected:
            pass
        else:
            raise AssertionError("fault schedule returned the deliberately lost response")
        assert board.submissions["qualification{ambiguous}"] == "accepted"
        _request(
            root,
            "/api/v1/plugins/ctfd-chall-manager/instance",
            method="POST",
            body={"challengeId": 1},
        )
        ledger = _request(root, "/api/v1/plugins/ctfd-chall-manager/instances?page=1")
        assert ledger["data"]["rows"][0]["instanceId"] == "controlled-instance-1"
        _request(
            root,
            "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=1",
            method="DELETE",
            body={"challengeId": 1},
        )
        assert board.instances == {}
        assert [row["method"] for row in board.observations][-4:] == ["POST", "POST", "GET", "DELETE"]
        assert all(row["authenticated"] for row in board.observations)
    finally:
        endpoint.shutdown()
        thread.join()
