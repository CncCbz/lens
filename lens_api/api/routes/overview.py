from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/overview-summary",
        service_module.overview_summary,
        methods=["GET"],
        response_model=service_module.OverviewSummary,
    )
    app.add_api_route(
        "/api/admin/overview-daily",
        service_module.overview_daily,
        methods=["GET"],
        response_model=list[service_module.OverviewDailyPoint],
    )
    app.add_api_route(
        "/api/admin/overview-models",
        service_module.overview_models,
        methods=["GET"],
        response_model=service_module.OverviewModelAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-channels",
        service_module.overview_channels,
        methods=["GET"],
        response_model=service_module.OverviewChannelAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-health/channels",
        service_module.overview_channel_health,
        methods=["GET"],
        response_model=list[service_module.OverviewChannelHealthPoint],
    )
    app.add_api_route(
        "/api/admin/overview-usage/channels",
        service_module.overview_usage_channels,
        methods=["GET"],
        response_model=service_module.OverviewDimensionUsageAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-usage/models",
        service_module.overview_usage_models,
        methods=["GET"],
        response_model=service_module.OverviewDimensionUsageAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-usage/gateway-keys",
        service_module.overview_usage_gateway_keys,
        methods=["GET"],
        response_model=service_module.OverviewDimensionUsageAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-performance/channels",
        service_module.overview_performance_channels,
        methods=["GET"],
        response_model=service_module.OverviewPerformanceAnalytics,
    )
    app.add_api_route(
        "/api/admin/overview-performance/models",
        service_module.overview_performance_models,
        methods=["GET"],
        response_model=service_module.OverviewPerformanceAnalytics,
    )
