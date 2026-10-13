from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
from zeep.helpers import serialize_object
from zeep.transports import Transport


class ConnectorError(RuntimeError):
    """Safe-to-display connector error."""


def validate_https_url(url: str, label: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ConnectorError(f"{label} must be a complete HTTPS URL.")
    if parsed.username or parsed.password:
        raise ConnectorError(f"{label} must not embed credentials in the URL.")
    return value


def _session(wsdl_auth_mode: str = "none", wsdl_username: str = "", wsdl_password: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Oracle-HCM-BI-Intelligence/0.1"})
    if wsdl_auth_mode == "http_basic":
        if not wsdl_username or not wsdl_password:
            raise ConnectorError("HTTP Basic was selected for WSDL retrieval, but its username/password are missing.")
        session.auth = HTTPBasicAuth(wsdl_username, wsdl_password)
    elif wsdl_auth_mode != "none":
        raise ConnectorError("Unsupported WSDL retrieval authentication mode.")
    return session


def test_wsdl(
    wsdl_url: str,
    wsdl_auth_mode: str = "none",
    wsdl_username: str = "",
    wsdl_password: str = "",
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Fetch only the exact WSDL URL supplied by the user; never guess endpoint paths."""
    wsdl_url = validate_https_url(wsdl_url, "WSDL URL")
    session = _session(wsdl_auth_mode, wsdl_username, wsdl_password)
    try:
        response = session.get(wsdl_url, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException as exc:
        raise ConnectorError(f"Could not reach the supplied WSDL URL ({type(exc).__name__}). Check network access and the URL.") from exc
    if response.url and urlparse(response.url).scheme.lower() != "https":
        raise ConnectorError("The WSDL URL redirected away from HTTPS; the request was stopped.")
    if response.status_code >= 400:
        detail = {
            401: "Authentication was rejected (401). Confirm the selected WSDL authentication mode and credentials.",
            403: "Access was forbidden (403). Confirm account permissions and any tenant/network restrictions.",
            404: "The supplied WSDL URL was not found (404). Confirm the exact WSDL URL with your administrator.",
        }.get(response.status_code, f"HTTP {response.status_code} was returned.")
        raise ConnectorError(detail)
    body_start = response.text[:10000].lower()
    if "definitions" not in body_start and "wsdl" not in body_start:
        content_type = response.headers.get("Content-Type", "unknown")
        raise ConnectorError(
            f"The URL responded successfully but did not look like a WSDL document (Content-Type: {content_type}). "
            "Supply the exact CatalogService WSDL URL."
        )
    try:
        transport = Transport(session=session, timeout=timeout_seconds, operation_timeout=timeout_seconds)
        client = Client(
            wsdl=wsdl_url,
            transport=transport,
            settings=Settings(strict=False, xml_huge_tree=False, forbid_dtd=True, forbid_entities=True),
        )
        operations: list[str] = []
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                operations.extend(port.binding._operations.keys())
        operations = sorted(set(operations))
    except Exception as exc:
        raise ConnectorError(
            f"The WSDL was reachable but could not be parsed ({type(exc).__name__}). "
            "The service contract may be incompatible or access may be restricted."
        ) from exc
    return {
        "wsdl_url": wsdl_url,
        "http_status": response.status_code,
        "operations": operations,
        "has_get_folder_contents": any(name.lower() == "getfoldercontents" for name in operations),
        "has_get_object": any(name.lower() == "getobject" for name in operations),
    }


def _client(
    wsdl_url: str,
    wsdl_auth_mode: str,
    wsdl_username: str,
    wsdl_password: str,
    service_endpoint: str | None,
    timeout_seconds: int,
) -> Client:
    wsdl_url = validate_https_url(wsdl_url, "WSDL URL")
    session = _session(wsdl_auth_mode, wsdl_username, wsdl_password)
    transport = Transport(session=session, timeout=timeout_seconds, operation_timeout=timeout_seconds)
    client = Client(
        wsdl=wsdl_url,
        transport=transport,
        settings=Settings(strict=False, xml_huge_tree=False, forbid_dtd=True, forbid_entities=True),
    )
    if service_endpoint:
        endpoint = validate_https_url(service_endpoint, "SOAP service endpoint")
        bindings = []
        for service in client.wsdl.services.values():
            for port in service.ports.values():
                bindings.append(port.binding.name.text)
        if not bindings:
            raise ConnectorError("The WSDL contains no SOAP bindings.")
        # An explicitly supplied override is applied to the first binding exposed by the WSDL.
        client.service = client.create_service(bindings[0], endpoint)
    return client


def _to_plain(value: Any) -> Any:
    return serialize_object(value, target_cls=dict)


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_dicts(child)


def _extract_items(result: Any) -> list[dict[str, Any]]:
    plain = _to_plain(result)
    candidates: list[dict[str, Any]] = []
    for item in _walk_dicts(plain):
        keys = {str(k).lower() for k in item.keys()}
        if keys.intersection({"absolutePath".lower(), "objectAbsolutePath".lower(), "path"}) and (
            keys.intersection({"objecttype", "type", "name", "displayname"})
        ):
            candidates.append(item)
    # Remove duplicates generated by nested response wrappers.
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in candidates:
        path = _first(item, ("absolutePath", "objectAbsolutePath", "path"))
        if path and str(path) not in seen:
            seen.add(str(path))
            output.append(item)
    return output


def _first(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in item.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] is not None:
            return lowered[name.lower()]
    return None


def _normalise_object(item: dict[str, Any], parent_path: str) -> dict[str, Any] | None:
    path = _first(item, ("absolutePath", "objectAbsolutePath", "path"))
    name = _first(item, ("name", "displayName", "objectName"))
    kind = _first(item, ("objectType", "type", "objectTypeName"))
    description = _first(item, ("description", "objectDescription"))
    if not path:
        return None
    path = str(path)
    name = str(name or path.rstrip("/").split("/")[-1])
    raw_kind = str(kind or "").strip()
    lower = raw_kind.lower()
    if lower in {"xdo", "report", "reportobject"} or path.lower().endswith(".xdo"):
        object_type = "report"
    elif lower in {"xdm", "datamodel", "data model", "data_model"} or path.lower().endswith(".xdm"):
        object_type = "data_model"
    elif "folder" in lower or path.endswith("/"):
        object_type = "folder"
    else:
        object_type = raw_kind or "unknown"
    return {
        "path": path,
        "name": name,
        "type": object_type,
        "description": str(description or ""),
        "parent_path": parent_path,
        "raw": item,
    }


def _safe_file_name(path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/"))[:160] or "catalog_object"
    return slug


def scan_catalog(
    *,
    wsdl_url: str,
    service_endpoint: str | None,
    wsdl_auth_mode: str,
    wsdl_username: str,
    wsdl_password: str,
    soap_username: str,
    soap_password: str,
    root_path: str,
    definitions_dir: Path,
    download_definitions: bool = True,
    max_objects: int = 500,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Read-only recursive catalogue scan using documented CatalogService methods."""
    if not root_path.strip().startswith("/"):
        raise ConnectorError("Catalog root path must be an absolute path beginning with '/'.")
    if not soap_username or not soap_password:
        raise ConnectorError("CatalogService SOAP username and password are required.")
    if max_objects < 1 or max_objects > 5000:
        raise ConnectorError("Maximum object count must be between 1 and 5000.")

    client = _client(
        wsdl_url, wsdl_auth_mode, wsdl_username, wsdl_password,
        service_endpoint, timeout_seconds
    )
    service = client.service
    if not hasattr(service, "getFolderContents"):
        raise ConnectorError("The supplied WSDL does not expose getFolderContents; catalog scan cannot continue.")
    if download_definitions and not hasattr(service, "getObject"):
        raise ConnectorError("Definition download was requested, but the supplied WSDL does not expose getObject.")

    definitions_dir.mkdir(parents=True, exist_ok=True)
    queue = [root_path.strip()]
    visited_folders: set[str] = set()
    seen_objects: set[str] = set()
    objects: list[dict[str, Any]] = []
    errors: list[str] = []

    while queue:
        folder_path = queue.pop(0)
        if folder_path in visited_folders:
            continue
        visited_folders.add(folder_path)
        try:
            result = service.getFolderContents(folder_path, soap_username, soap_password)
            items = _extract_items(result)
        except Exception as exc:
            errors.append(f"{folder_path}: getFolderContents failed ({type(exc).__name__}).")
            continue

        if not items and result is not None:
            # Preserve a visible diagnostic rather than silently claiming an empty catalogue.
            plain = _to_plain(result)
            if plain:
                errors.append(f"{folder_path}: response shape was not recognized; no objects extracted.")

        for raw_item in items:
            normal = _normalise_object(raw_item, folder_path)
            if not normal:
                continue
            path = normal["path"]
            if path in seen_objects:
                continue
            seen_objects.add(path)
            objects.append(normal)
            if len(objects) >= max_objects:
                return {
                    "objects": objects,
                    "folders_seen": len(visited_folders),
                    "truncated": True,
                    "errors": errors,
                    "message": f"Stopped at the safety limit of {max_objects} catalogue objects. Increase the limit and scan again if needed.",
                }

            if normal["type"] == "folder":
                queue.append(path if path.endswith("/") else path + "/")

            if download_definitions and normal["type"] in {"report", "data_model"}:
                try:
                    blob = service.getObject(path, "en_US", soap_username, soap_password)
                    if blob:
                        payload = bytes(blob) if not isinstance(blob, bytes) else blob
                        digest = hashlib.sha256(payload).hexdigest()
                        filename = _safe_file_name(path) + (".xdo" if normal["type"] == "report" else ".xdm")
                        target = definitions_dir / filename
                        target.write_bytes(payload)
                        normal["definition_file"] = str(target)
                        normal["content_fingerprint"] = digest
                except Exception as exc:
                    errors.append(f"{path}: definition download failed ({type(exc).__name__}).")

    return {
        "objects": objects,
        "folders_seen": len(visited_folders),
        "truncated": False,
        "errors": errors,
        "message": f"Scan completed: {len(objects)} objects discovered across {len(visited_folders)} folders.",
    }
