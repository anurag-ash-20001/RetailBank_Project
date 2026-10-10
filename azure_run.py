"""
azure_run.py — run the pipeline inside Azure (Container Apps Job).

Flow:
    1. download raw feeds from Blob container  `raw`     -> RetailBank_SourceData*/
    2. run main.main() unchanged
    3. upload output/ to Blob container         `output`  -> runs/<timestamp>/... and latest/...

Auth uses DefaultAzureCredential (the job's managed identity in Azure, or your
`az login` session locally). Required env var:
    AZURE_STORAGE_ACCOUNT_URL   e.g. https://retailbankdata123.blob.core.windows.net
Optional:
    RAW_CONTAINER (default "raw"), OUTPUT_CONTAINER (default "output")
"""
import os
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

import config as cfg
import main as pipeline

RAW_PREFIXES = [os.path.basename(cfg.DAY1_DIR), os.path.basename(cfg.DAY2_DIR)]


def log(m): print(m, flush=True)


def download_raw(svc, container):
    cc = svc.get_container_client(container)
    n = 0
    for prefix in RAW_PREFIXES:
        for blob in cc.list_blobs(name_starts_with=prefix + "/"):
            dest = os.path.join(cfg.BASE_DIR, *blob.name.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(cc.download_blob(blob.name).readall())
            n += 1
    log(f"Downloaded {n} raw file(s) from '{container}'")


def upload_output(svc, container):
    cc = svc.get_container_client(container)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    for root, _, files in os.walk(cfg.OUTPUT_DIR):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, cfg.OUTPUT_DIR).replace(os.sep, "/")
            for prefix in (f"runs/{run_id}", "latest"):
                with open(path, "rb") as f:
                    cc.upload_blob(f"{prefix}/{rel}", f, overwrite=True)
            n += 1
    log(f"Uploaded {n} output file(s) to '{container}' under runs/{run_id}/ and latest/")


def run():
    svc = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"],
                            credential=DefaultAzureCredential())
    download_raw(svc, os.environ.get("RAW_CONTAINER", "raw"))
    pipeline.main()
    upload_output(svc, os.environ.get("OUTPUT_CONTAINER", "output"))


if __name__ == "__main__":
    run()
