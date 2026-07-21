from __future__ import annotations

from agentos.transports.a2a.card_types import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentExtension,
    A2AAgentInterface,
    A2AAgentSkill,
    A2AApiKeySecurityScheme,
    A2AAuthorizationCodeOAuthFlow,
    A2AClientCredentialsOAuthFlow,
    A2ADeviceCodeOAuthFlow,
    A2AHttpAuthSecurityScheme,
    A2AImplicitOAuthFlow,
    A2AMutualTlsSecurityScheme,
    A2AOAuth2SecurityScheme,
    A2AOAuthFlows,
    A2AOpenIdConnectSecurityScheme,
    A2APasswordOAuthFlow,
    A2ASecurityRequirement,
    A2ASecurityScheme,
)
from agentos.transports.a2a.push_types import (
    A2AAuthenticationInfo,
    A2ATaskPushNotificationConfig,
)
from agentos.transports.a2a.serialization import (
    agent_card_from_dict,
    agent_card_to_dict,
)


def _card(**changes: object) -> A2AAgentCard:
    values: dict[str, object] = {
        "name": "reviewer",
        "description": "reviews code",
        "supported_interfaces": (
            A2AAgentInterface(
                url="https://agent.example/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        "version": "2.1.0",
        "capabilities": A2AAgentCapabilities(
            streaming=False,
            extensions=(
                A2AAgentExtension(
                    "https://agentos.dev/a2a/extensions/snapshot-resume/v1",
                    params={"nested": {"enabled": True}},
                ),
            ),
        ),
        "default_input_modes": ("text/plain",),
        "default_output_modes": ("text/plain",),
        "skills": (
            A2AAgentSkill(
                id="review",
                name="Review",
                description="Review code",
                tags=("code",),
            ),
        ),
    }
    values.update(changes)
    return A2AAgentCard(**values)  # type: ignore[arg-type]


def test_agent_card_is_deeply_frozen_preserves_presence_and_round_trips() -> None:
    card = _card()

    payload = agent_card_to_dict(card)

    assert payload["capabilities"]["streaming"] is False
    assert "pushNotifications" not in payload["capabilities"]
    assert payload["capabilities"]["extensions"][0]["params"] == {
        "nested": {"enabled": True},
    }
    assert "required" not in payload["capabilities"]["extensions"][0]
    assert "url" not in payload
    assert "protocolVersion" not in payload
    assert agent_card_from_dict(payload) == card


def test_card_security_scheme_oneofs_round_trip() -> None:
    scopes = {"read": "Read tasks"}
    schemes = {
        "api": A2ASecurityScheme(
            api_key=A2AApiKeySecurityScheme(location="header", name="X-API-Key"),
        ),
        "http": A2ASecurityScheme(
            http_auth=A2AHttpAuthSecurityScheme(scheme="Bearer", bearer_format="JWT"),
        ),
        "oauth-authorization": A2ASecurityScheme(
            oauth2=A2AOAuth2SecurityScheme(
                flows=A2AOAuthFlows(
                    authorization_code=A2AAuthorizationCodeOAuthFlow(
                        authorization_url="https://auth.example/authorize",
                        token_url="https://auth.example/token",
                        scopes=scopes,
                        pkce_required=True,
                    ),
                ),
            ),
        ),
        "oauth-client": A2ASecurityScheme(
            oauth2=A2AOAuth2SecurityScheme(
                flows=A2AOAuthFlows(
                    client_credentials=A2AClientCredentialsOAuthFlow(
                        token_url="https://auth.example/token",
                        scopes=scopes,
                    ),
                ),
            ),
        ),
        "oauth-implicit": A2ASecurityScheme(
            oauth2=A2AOAuth2SecurityScheme(
                flows=A2AOAuthFlows(
                    implicit=A2AImplicitOAuthFlow(
                        authorization_url="https://auth.example/authorize",
                        scopes=scopes,
                    ),
                ),
            ),
        ),
        "oauth-password": A2ASecurityScheme(
            oauth2=A2AOAuth2SecurityScheme(
                flows=A2AOAuthFlows(
                    password=A2APasswordOAuthFlow(
                        token_url="https://auth.example/token",
                        scopes=scopes,
                    ),
                ),
            ),
        ),
        "oauth-device": A2ASecurityScheme(
            oauth2=A2AOAuth2SecurityScheme(
                flows=A2AOAuthFlows(
                    device_code=A2ADeviceCodeOAuthFlow(
                        device_authorization_url="https://auth.example/device",
                        token_url="https://auth.example/token",
                        scopes=scopes,
                    ),
                ),
            ),
        ),
        "oidc": A2ASecurityScheme(
            open_id_connect=A2AOpenIdConnectSecurityScheme(
                open_id_connect_url="https://auth.example/.well-known/openid-configuration",
            ),
        ),
        "mtls": A2ASecurityScheme(mtls=A2AMutualTlsSecurityScheme()),
    }
    card = _card(
        security_schemes=schemes,
        security_requirements=(A2ASecurityRequirement({"oauth-device": ("read",)}),),
    )

    payload = agent_card_to_dict(card)

    assert payload["securityRequirements"] == [
        {"schemes": {"oauth-device": {"list": ["read"]}}},
    ]
    assert agent_card_from_dict(payload) == card
    scopes["write"] = "Write tasks"
    assert (
        "write"
        not in agent_card_to_dict(card)["securitySchemes"]["oauth-authorization"][
            "oauth2SecurityScheme"
        ]["flows"]["authorizationCode"]["scopes"]
    )


def test_push_credentials_are_redacted_from_repr() -> None:
    authentication = A2AAuthenticationInfo(
        scheme="Bearer",
        credentials="secret-credential",
    )
    config = A2ATaskPushNotificationConfig(
        id="push_1",
        task_id="run_1",
        url="https://push.example/events",
        token="secret-token",
        authentication=authentication,
    )

    rendered = repr(config)
    assert "secret-credential" not in rendered
    assert "secret-token" not in rendered
    assert "<redacted>" in rendered
