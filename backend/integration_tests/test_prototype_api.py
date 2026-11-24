from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_execution_vertical import drain_inline

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.execution_models import ExecutionOperation
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.presentation.api import create_app


def key():
    return {"Idempotency-Key": str(uuid4())}


@pytest.fixture
def admin():
    with TestClient(create_app()) as client:
        assert client.get("/api/prototype-experiments").status_code == 401
        response = client.post("/api/auth/session", json={"admin_key": get_settings().admin_access_key})
        assert response.status_code == 200
        assert get_settings().session_cookie_name in response.headers["set-cookie"]
        yield client


def create(admin, case, **patch):
    payload = {name: value for name, value in case.items() if name != "product_id"}
    response = admin.post(
        f"/api/products/{case['product_id']}/prototype-tasks", json={**payload, **patch}, headers=key()
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("language", ["zh-CN", "ms-MY", "id-ID", "fil-PH"])
def test_content_language_preview_confirmation_and_stale_version(admin, synthetic_case, language):
    task = create(admin, synthetic_case, content_language=language)
    path = f"/api/prototype-tasks/{task['id']}"
    assert (
        admin.post(path + "/text-preparations", json={"content_language": language}, headers=key()).status_code == 422
    )
    body = {"expected_row_version": task["row_version"], "content_language": language}
    assert admin.post(path + "/text-preparations", json=body).status_code == 422
    headers = key()
    submitted = admin.post(path + "/text-preparations", json=body, headers=headers)
    assert submitted.status_code == 202, submitted.text
    drain_inline([submitted.json()["operation_id"]])
    current = admin.get(path).json()
    plan = current["plans"][-1]
    assert len(plan["slots"]) == 10 and plan["content_language"] == language
    assert admin.post(path + "/text-preparations", json=body, headers=headers).json() == submitted.json()
    query = admin.get(
        f"/api/execution-requests/{headers['Idempotency-Key']}",
        params={"root_kind": "prototype", "root_id": task["id"], "action": "text-preparations"},
    ).json()
    assert query["status"] == "committed" and query["response"] == submitted.json()
    confirmation = {
        "expected_row_version": current["row_version"],
        "plan_id": plan["id"],
        "plan_sha256": plan["input_sha256"],
        "preview_revision": plan["preview_revision"],
        "content_language": language,
    }
    for patch in (
        {"expected_row_version": 0},
        {"plan_sha256": "0" * 64},
        {"preview_revision": 999},
        {"content_language": "id-ID" if language != "id-ID" else "zh-CN"},
    ):
        assert admin.post(path + "/confirm", json={**confirmation, **patch}, headers=key()).status_code in {409, 422}
    assert admin.post(path + "/confirm", json=confirmation, headers=key()).status_code == 200
    # Canvas/UI language settings are absent from this immutable content contract.
    assert admin.get(path).json()["plans"][-1]["content_language"] == language


def test_late_text_keeps_original_revision_without_replacing_changed_draft(admin, synthetic_case):
    task = create(admin, synthetic_case)
    path = f"/api/prototype-tasks/{task['id']}"
    prepared = admin.post(
        path + "/text-preparations", json={"expected_row_version": 1, "content_language": "zh-CN"}, headers=key()
    )
    draft = {name: value for name, value in synthetic_case.items() if name not in {"product_id", "experiment_run_id"}}
    changed = admin.patch(
        path + "/draft", json={**draft, "content_language": "fil-PH", "expected_row_version": 2}, headers=key()
    )
    assert changed.status_code == 200, changed.text
    drain_inline([prepared.json()["operation_id"]])
    current = admin.get(path).json()
    assert current["draft_content_language"] == "fil-PH" and current["current_plan_id"] is None
    assert current["plans"][0]["content_language"] == "zh-CN"
    assert current["plans"][0]["source_draft_revision"] < current["draft_revision"]


def test_cross_resource_and_disabled_current_policy_do_not_revoke_accepted_execution(
    admin, synthetic_case, monkeypatch
):
    task = create(admin, synthetic_case)
    path = f"/api/prototype-tasks/{task['id']}"
    response = admin.post(
        path + "/text-preparations", json={"expected_row_version": 1, "content_language": "zh-CN"}, headers=key()
    )
    operation_id = response.json()["operation_id"]
    assert (
        admin.post(
            f"/api/image-sessions/{uuid4()}/generation-tasks/{uuid4()}/operations/{operation_id}/close-wait",
            json={"expected_row_version": 1},
            headers=key(),
        ).status_code
        == 404
    )
    monkeypatch.setattr(
        "productflow_backend.presentation.deps.get_runtime_settings",
        lambda: get_settings().model_copy(update={"admin_access_required": False}),
    )
    assert (
        admin.post(
            path + "/text-preparations", json={"expected_row_version": 2, "content_language": "zh-CN"}, headers=key()
        ).status_code
        == 403
    )
    drain_inline([operation_id])
    with get_session_factory()() as session:
        assert (
            session.scalar(select(ExecutionOperation.state).where(ExecutionOperation.id == operation_id)) == "succeeded"
        )
