"""Azure Functions entry point: POST /sas (route prefix disabled in host.json)."""

from __future__ import annotations

import json

import azure.functions as func

from broker.azure_api import DelegationKeyCache
from broker.handler import Broker, Config

_config = Config.from_env()
_keys = DelegationKeyCache(_config.blob_endpoint, _config.identity_client_id)
_broker = Broker(_config, _keys.get)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="sas", methods=["POST"])
def sas(req: func.HttpRequest) -> func.HttpResponse:
    status, body = _broker.handle(req.headers.get("Authorization"))
    return func.HttpResponse(
        json.dumps(body),
        status_code=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )
