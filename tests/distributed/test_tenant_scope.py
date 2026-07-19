import pytest

from agentos.distributed.models import RequestScope


def test_request_scope_requires_authenticated_identity() -> None:
    scope = RequestScope(tenant_id="tenant_1", principal_id="user_1")

    assert scope.tenant_id == "tenant_1"
    assert scope.principal_id == "user_1"


@pytest.mark.parametrize("field_name", ["tenant_id", "principal_id"])
def test_request_scope_rejects_empty_identity(field_name: str) -> None:
    values = {"tenant_id": "tenant_1", "principal_id": "user_1"}
    values[field_name] = " "

    with pytest.raises(ValueError, match=field_name):
        RequestScope(**values)
