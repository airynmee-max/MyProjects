from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from connector import ConnectorError, scan_catalog, test_wsdl
from storage import (
    definitions_dir,
    finish_scan,
    get_environment,
    init_db,
    list_catalog_objects,
    list_environments,
    list_scans,
    save_environment,
    start_scan,
    update_test_result,
    upsert_catalog_object,
)

load_dotenv()

st.set_page_config(
    page_title="Oracle HCM BI Intelligence",
    page_icon="🧭",
    layout="wide",
)

init_db()

st.title("Oracle HCM BI Intelligence")
st.caption("Milestone 1 · Environment connections and BI Catalog discovery")
st.warning(
    "Prototype: use a non-production environment first. This app performs catalogue read operations only. "
    "Do not enter client credentials unless you trust the machine running this app."
)

if not os.getenv("APP_ENCRYPTION_KEY", "").strip():
    st.error(
        "APP_ENCRYPTION_KEY is not configured. Copy .env.example to .env, generate a Fernet key, "
        "and restart the app before saving credentials."
    )

tab_connect, tab_catalog, tab_history = st.tabs(["Environment connections", "Catalogue inventory", "Scan history"])

with tab_connect:
    st.subheader("Add or update an environment")
    st.write(
        "Enter the exact CatalogService WSDL URL provided or confirmed by your Oracle administrator. "
        "The application does not guess service paths or test a URL you have not supplied."
    )
    with st.form("environment_form", clear_on_submit=False):
        name = st.text_input("Environment name", placeholder="Client A — TEST")
        wsdl_url = st.text_input("Exact CatalogService WSDL URL", placeholder="https://.../CatalogService?wsdl")
        service_endpoint = st.text_input(
            "SOAP endpoint override (optional)",
            placeholder="Leave blank to use the endpoint advertised by the WSDL",
            help="Only enter this if your administrator confirms the correct SOAP endpoint. It is not auto-discovered.",
        )
        wsdl_auth_mode = st.selectbox(
            "Authentication to retrieve WSDL",
            options=["none", "http_basic"],
            format_func=lambda v: "No HTTP authentication" if v == "none" else "HTTP Basic (only if WSDL access requires it)",
        )
        wsdl_username = st.text_input("WSDL HTTP Basic username (optional)", disabled=wsdl_auth_mode == "none")
        wsdl_password = st.text_input("WSDL HTTP Basic password (optional)", type="password", disabled=wsdl_auth_mode == "none")
        st.markdown("**CatalogService operation credentials**")
        soap_username = st.text_input("BI Publisher username")
        soap_password = st.text_input("BI Publisher password", type="password")
        save_clicked = st.form_submit_button("Save environment securely", type="primary")

    if save_clicked:
        if not os.getenv("APP_ENCRYPTION_KEY", "").strip():
            st.error("Cannot save credentials until APP_ENCRYPTION_KEY is configured.")
        elif not name.strip() or not wsdl_url.strip() or not soap_username.strip() or not soap_password:
            st.error("Environment name, WSDL URL, BI Publisher username, and BI Publisher password are required.")
        else:
            try:
                env_id = save_environment(
                    name=name.strip(),
                    wsdl_url=wsdl_url.strip(),
                    service_endpoint=service_endpoint.strip() or None,
                    wsdl_auth_mode=wsdl_auth_mode,
                    wsdl_username=wsdl_username.strip() or None,
                    wsdl_password=wsdl_password,
                    soap_username=soap_username.strip(),
                    soap_password=soap_password,
                )
                st.success(f"Environment saved securely (ID {env_id}).")
            except Exception as exc:
                st.error(f"Could not save environment: {exc}")

    envs = list_environments()
    if envs:
        st.divider()
        st.subheader("Registered environments")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Name": e["name"],
                        "WSDL URL": e["wsdl_url"],
                        "WSDL auth": e["wsdl_auth_mode"],
                        "Last test": e["last_test_status"] or "Not tested",
                        "Last test time": e["last_test_at"] or "—",
                    }
                    for e in envs
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        selected_for_test = st.selectbox(
            "Environment to test",
            options=[e["id"] for e in envs],
            format_func=lambda i: next(e["name"] for e in envs if e["id"] == i),
            key="test_env_id",
        )
        if st.button("Test WSDL and inspect operations"):
            try:
                env = get_environment(selected_for_test, include_secrets=True)
                result = test_wsdl(
                    env["wsdl_url"],
                    env["wsdl_auth_mode"],
                    env.get("wsdl_username") or "",
                    env.get("wsdl_password") or "",
                )
                msg = (
                    f"WSDL reachable (HTTP {result['http_status']}); "
                    f"{len(result['operations'])} operations inspected; "
                    f"getFolderContents={'yes' if result['has_get_folder_contents'] else 'no'}; "
                    f"getObject={'yes' if result['has_get_object'] else 'no'}."
                )
                update_test_result(selected_for_test, "passed" if result["has_get_folder_contents"] else "partial", msg)
                st.success(msg)
                with st.expander("WSDL operations discovered"):
                    st.code("\n".join(result["operations"]) or "(No operations discovered)")
                if not result["has_get_folder_contents"]:
                    st.warning("This WSDL does not expose getFolderContents. Do not scan until the correct service contract is confirmed.")
            except ConnectorError as exc:
                update_test_result(selected_for_test, "failed", str(exc))
                st.error(str(exc))
            except Exception as exc:
                update_test_result(selected_for_test, "failed", f"Unexpected error ({type(exc).__name__}).")
                st.error(f"Unexpected error ({type(exc).__name__}). Check app logs; sensitive values are not shown.")

with tab_catalog:
    st.subheader("Read-only catalogue scan")
    envs = list_environments()
    if not envs:
        st.info("Register an environment in the first tab before scanning.")
    else:
        selected_env_id = st.selectbox(
            "Environment to scan",
            options=[e["id"] for e in envs],
            format_func=lambda i: next(e["name"] for e in envs if e["id"] == i),
            key="scan_env_id",
        )
        root_path = st.text_input(
            "Catalog root path",
            value="/",
            help="Confirm the correct root path and read permissions with your administrator.",
        )
        col1, col2 = st.columns(2)
        with col1:
            download_definitions = st.checkbox(
                "Download report/data model definitions",
                value=False,
                help="Saves definition files locally for later SQL/data-model analysis. Treat them as client-confidential.",
            )
        with col2:
            max_objects = st.number_input("Safety limit (objects)", min_value=1, max_value=5000, value=300, step=50)
        if st.button("Scan Catalog", type="primary"):
            env = get_environment(selected_env_id, include_secrets=True)
            if not env:
                st.error("Environment not found.")
            else:
                scan_id = start_scan(selected_env_id, root_path.strip())
                try:
                    with st.spinner("Reading catalogue folders. This may take some time..."):
                        result = scan_catalog(
                            wsdl_url=env["wsdl_url"],
                            service_endpoint=env.get("service_endpoint"),
                            wsdl_auth_mode=env["wsdl_auth_mode"],
                            wsdl_username=env.get("wsdl_username") or "",
                            wsdl_password=env.get("wsdl_password") or "",
                            soap_username=env["soap_username"],
                            soap_password=env["soap_password"],
                            root_path=root_path.strip(),
                            definitions_dir=definitions_dir() / str(selected_env_id),
                            download_definitions=download_definitions,
                            max_objects=int(max_objects),
                        )
                    for obj in result["objects"]:
                        upsert_catalog_object(
                            selected_env_id,
                            object_path=obj["path"],
                            object_name=obj["name"],
                            object_type=obj["type"],
                            description=obj["description"],
                            parent_path=obj["parent_path"],
                            raw_metadata=obj["raw"],
                            definition_file=obj.get("definition_file"),
                            content_fingerprint=obj.get("content_fingerprint"),
                        )
                    status = "partial" if result["errors"] or result["truncated"] else "completed"
                    finish_scan(
                        scan_id, status, len(result["objects"]), result["folders_seen"],
                        "; ".join(result["errors"]) if result["errors"] else None,
                    )
                    st.success(result["message"])
                    if result["truncated"]:
                        st.warning("The scan reached its safety limit. Increase it and scan again to continue coverage.")
                    if result["errors"]:
                        st.warning(f"{len(result['errors'])} issue(s) occurred. Review the details below.")
                        with st.expander("Scan warnings"):
                            st.write("\n".join(result["errors"]))
                    st.rerun()
                except ConnectorError as exc:
                    finish_scan(scan_id, "failed", 0, 0, str(exc))
                    st.error(str(exc))
                except Exception as exc:
                    finish_scan(scan_id, "failed", 0, 0, f"Unexpected error ({type(exc).__name__})")
                    st.error(f"Scan failed ({type(exc).__name__}). Check app logs and confirm the WSDL/endpoint contract.")

        objects = list_catalog_objects(selected_env_id, limit=5000)
        if objects:
            st.divider()
            st.subheader(f"Stored catalogue objects ({len(objects)})")
            df = pd.DataFrame(
                [
                    {
                        "Type": obj["object_type"],
                        "Name": obj["object_name"],
                        "Catalog path": obj["object_path"],
                        "Description": obj["description"],
                        "Definition downloaded": "Yes" if obj["definition_file"] else "No",
                        "Last seen": obj["last_seen_at"],
                    }
                    for obj in objects
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button(
                "Export inventory as CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="bi_catalog_inventory.csv",
                mime="text/csv",
            )
        else:
            st.info("No catalogue objects have been stored for this environment yet.")

with tab_history:
    st.subheader("Scan history")
    envs = list_environments()
    if not envs:
        st.info("No environments have been registered.")
    else:
        history_env_id = st.selectbox(
            "Environment history",
            options=[e["id"] for e in envs],
            format_func=lambda i: next(e["name"] for e in envs if e["id"] == i),
            key="history_env_id",
        )
        scans = list_scans(history_env_id)
        if scans:
            st.dataframe(pd.DataFrame(scans), use_container_width=True, hide_index=True)
        else:
            st.info("No scans have been run for this environment.")
