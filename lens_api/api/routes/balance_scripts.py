from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/balance-scripts",
        service_module.list_balance_scripts,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/admin/balance-scripts",
        service_module.create_balance_script,
        methods=["POST"],
        status_code=201,
    )
    app.add_api_route(
        "/api/admin/balance-scripts/test",
        service_module.test_balance_script,
        methods=["POST"],
    )
    app.add_api_route(
        "/api/admin/balance-scripts/{script_id}",
        service_module.update_balance_script,
        methods=["PUT"],
    )
    app.add_api_route(
        "/api/admin/balance-scripts/{script_id}",
        service_module.delete_balance_script,
        methods=["DELETE"],
        status_code=204,
    )
    app.add_api_route(
        "/api/admin/credential-balances",
        service_module.list_credential_balances,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/admin/sites/{site_id}/credentials/{credential_id}/balance",
        service_module.refresh_credential_balance,
        methods=["POST"],
    )
