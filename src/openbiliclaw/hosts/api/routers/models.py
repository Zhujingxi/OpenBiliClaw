from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from openbiliclaw.ai.providers.catalog import (
    CapabilityConfig,
    CatalogError,
    UnsupportedProtocolError,
    protocol_for,
    resolve_model,
)
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities

from ..dependencies import HostDependencies, get_dependencies
from ..model_configuration import ModelConfigurationError, ModelUpdate
from ..schemas.models import (
    CapabilityResponse,
    CatalogModelResponse,
    CatalogProviderResponse,
    CurrentModelResponse,
    EmbeddingConfigurationResponse,
    ModelCatalogResponse,
    ModelConfigurationRequest,
    ModelConfigurationResponse,
    ModelConfigurationView,
)

router = APIRouter(prefix="/models", tags=["models"])


def _capabilities(value: ModelCapabilities) -> CapabilityResponse:
    return CapabilityResponse(
        tools=value.tools,
        structured_output=value.structured_output,
        vision=value.vision,
        context_tokens=value.context_tokens,
        streaming=value.streaming,
        reasoning=value.reasoning,
    )


def _current(
    dependencies: HostDependencies, *, restart_required: bool = False
) -> ModelConfigurationResponse:
    assert dependencies.models is not None
    settings = dependencies.models.settings()
    catalog = dependencies.models.catalog()
    model = settings.model
    protocol = None
    capabilities = None
    if model.model_name:
        resolved = resolve_model(
            catalog,
            provider_id=model.provider,
            model_name=model.model_name,
            endpoint=model.endpoint,
            protocol=model.protocol,
            capabilities=(
                None
                if model.capabilities is None
                else CapabilityConfig.model_validate(model.capabilities.model_dump())
            ),
        )
        protocol = resolved.protocol
        capabilities = _capabilities(resolved.capabilities)
    return ModelConfigurationResponse(
        current=CurrentModelResponse(
            model=ModelConfigurationView(
                provider=model.provider,
                model_name=model.model_name,
                endpoint=model.endpoint,
                secret_configured=model.secret_ref is not None,
                protocol=protocol,
                capabilities=capabilities,
            ),
            embedding=EmbeddingConfigurationResponse(
                provider=settings.embedding.provider,
                model_name=settings.embedding.model_name,
                endpoint=settings.embedding.endpoint,
                secret_configured=settings.embedding.secret_ref is not None,
            ),
        ),
        reloaded=False,
        restart_required=restart_required,
    )


@router.get("/catalog", response_model=ModelCatalogResponse)
def catalog(dependencies: HostDependencies = Depends(get_dependencies)) -> ModelCatalogResponse:
    if dependencies.models is None:
        raise HTTPException(status_code=503, detail="model catalog is not configured")
    try:
        loaded = dependencies.models.catalog()
    except CatalogError as error:
        raise HTTPException(status_code=503, detail="model catalog is unavailable") from error
    providers = []
    for provider in loaded.root.values():
        try:
            protocol = protocol_for(provider.npm)
        except UnsupportedProtocolError:
            continue
        providers.append(
            CatalogProviderResponse(
                id=provider.id,
                name=provider.name or provider.id,
                env=provider.env,
                protocol=protocol,
                models=tuple(
                    CatalogModelResponse(
                        id=model.id,
                        name=model.name or model.id,
                        reasoning=model.reasoning,
                        tool_call=model.tool_call,
                        structured_output=model.structured_output,
                        context_limit=model.limit.context,
                    )
                    for model in provider.models.values()
                ),
            )
        )
    return ModelCatalogResponse(providers=tuple(providers))


@router.get("/current", response_model=ModelConfigurationResponse)
def current(
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ModelConfigurationResponse:
    if dependencies.models is None:
        raise HTTPException(status_code=503, detail="model configuration is not configured")
    try:
        return _current(dependencies)
    except CatalogError as error:
        raise HTTPException(status_code=503, detail="model catalog is unavailable") from error


@router.put("/current", response_model=ModelConfigurationResponse)
def update(
    request: ModelConfigurationRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ModelConfigurationResponse:
    if dependencies.models is None:
        raise HTTPException(status_code=503, detail="model configuration is not configured")
    try:
        dependencies.models.update(
            ModelUpdate(
                provider=request.provider,
                model_name=request.model_name,
                endpoint=request.endpoint,
                protocol=request.protocol,
                capabilities=request.capabilities,
                api_key=request.api_key.get_secret_value() if request.api_key is not None else "",
            )
        )
    except ModelConfigurationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _current(dependencies, restart_required=True)
