from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    # The key lives in the body: it can contain `/`, and proxies disagree on
    # whether a path `%2F` stays encoded, which broke the path-parameter route.
    app.add_api_route(
        "/api/admin/model-prices",
        service_module.update_model_price,
        methods=["PUT"],
        response_model=service_module.ModelPriceItem,
    )
    app.add_api_route(
        "/api/admin/model-price-sync-jobs",
        service_module.sync_model_prices,
        methods=["POST"],
        response_model=service_module.ModelPriceListResponse,
    )
