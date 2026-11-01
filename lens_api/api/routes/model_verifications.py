from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/model-verifications",
        service_module.start_verification,
        methods=["POST"],
        response_model=service_module.ModelVerificationRun,
        status_code=202,
    )
    app.add_api_route(
        "/api/admin/model-verifications/runs/{run_id}",
        service_module.get_verification_run,
        methods=["GET"],
        response_model=service_module.ModelVerificationRun,
    )
    app.add_api_route(
        "/api/admin/model-verifications",
        service_module.list_verifications,
        methods=["GET"],
        response_model=service_module.ModelVerificationPage,
    )
    app.add_api_route(
        "/api/admin/model-verifications/latest",
        service_module.latest_verifications,
        methods=["GET"],
        response_model=list[service_module.ModelVerificationLatest],
    )
