"""Export deterministic OpenAPI without constructing production resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Never

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import AccessStatus
from openbiliclaw.application.content_actions import (
    ConfirmContentActionCommand,
    PendingAction,
    ProposeContentActionCommand,
)
from openbiliclaw.application.edit_profile import EditProfileCommand, EditProfileResult
from openbiliclaw.application.reads import (
    ContentDetailsResult,
    JobHealthResult,
    ProfileResult,
    RecommendationsResult,
    SearchContentResult,
    SourcesResult,
    SourceStatusResult,
)
from openbiliclaw.application.record_feedback import RecordFeedbackCommand, RecordFeedbackResult
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.refresh_recommendations import (
    RefreshRecommendationsCommand,
    RefreshRecommendationsResult,
)
from openbiliclaw.application.sources import (
    ConnectSourceCommand,
    ConnectSourceResult,
    DisconnectSourceCommand,
)
from openbiliclaw.assistant.models import AssistantOutput, Conversation, ConversationMessage
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.content.integration.projections import CardData
from openbiliclaw.hosts.api import HostDependencies, create_app
from openbiliclaw.hosts.api.dependencies import (
    AssistantTurnInput,
    DiagnosticResult,
    StartResult,
)
from openbiliclaw.observations.service import RecordBatchResult


class _SchemaFacade:
    """Complete typed facade whose methods cannot run during schema export."""

    def _unavailable(self) -> Never:
        raise RuntimeError("schema-export facade is unavailable")

    async def source_status(self, provider_id: str, account_id: str | None) -> SourceStatusResult:
        self._unavailable()

    async def source_form(self, provider_id: str, method_id: str) -> ConnectionForm:
        self._unavailable()

    async def list_sources(self, account_id: str | None, limit: int) -> SourcesResult:
        self._unavailable()

    def provider_capabilities(self, provider_id: str) -> tuple[str, ...]:
        self._unavailable()

    async def connect_source(self, command: ConnectSourceCommand) -> ConnectSourceResult:
        self._unavailable()

    async def disconnect_source(self, command: DisconnectSourceCommand) -> AccessStatus:
        self._unavailable()

    async def get_recommendations(self, limit: int) -> RecommendationsResult:
        self._unavailable()

    async def refresh_recommendations(
        self, command: RefreshRecommendationsCommand
    ) -> RefreshRecommendationsResult:
        self._unavailable()

    async def record_feedback(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        self._unavailable()

    async def record_observations(self, command: RecordObservationsCommand) -> RecordBatchResult:
        self._unavailable()

    async def show_profile(self, profile_id: str) -> ProfileResult:
        self._unavailable()

    async def edit_profile(self, command: EditProfileCommand) -> EditProfileResult:
        self._unavailable()

    async def search_content(self, provider_id: str, text: str, limit: int) -> SearchContentResult:
        self._unavailable()

    async def get_content_details(self, reference: str) -> ContentDetailsResult:
        self._unavailable()

    async def propose_action(self, command: ProposeContentActionCommand) -> PendingAction:
        self._unavailable()

    async def confirm_action(self, command: ConfirmContentActionCommand) -> ActionResult:
        self._unavailable()

    async def assistant_turn(self, request: AssistantTurnInput, device_id: str) -> AssistantOutput:
        self._unavailable()

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        self._unavailable()

    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> tuple[ConversationMessage, ...]:
        self._unavailable()

    async def job_health(self) -> JobHealthResult:
        self._unavailable()

    async def config_diagnostics(self) -> DiagnosticResult:
        self._unavailable()

    async def model_diagnostics(self) -> DiagnosticResult:
        self._unavailable()

    async def start(self) -> StartResult:
        self._unavailable()


def export(path: Path) -> None:
    schema = create_app(HostDependencies(facade=_SchemaFacade())).openapi()
    # CardData is the canonical presentation DTO even before a dedicated card
    # endpoint lands; include it so generated clients never duplicate it.
    card_schema = CardData.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = card_schema.get("$defs", {})
    if not isinstance(definitions, dict):
        raise TypeError("CardData schema definitions must be an object")
    schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions)
    schema["components"]["schemas"]["CardData"] = {
        key: value for key, value in card_schema.items() if key != "$defs"
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("frontend/packages/api-client/openapi.json")
    )
    args = parser.parse_args()
    export(args.output)


if __name__ == "__main__":
    main()
