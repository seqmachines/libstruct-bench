"""A compact searchable catalog. SQLite is also the offline development backend."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator

from .versions import version

from .storage import canonical

TABLES = ("experiments", "runs", "trials", "source_bundles", "snapshots")


@lru_cache(maxsize=None)
def document_schema(table):
    schemas = json.loads(
        files(__package__).joinpath("catalog.schema.json").read_bytes()
    )["$defs"]
    return Draft202012Validator(schemas[table])


def current_snapshots(documents):
    by_trial = {}

    def order(row):
        return (
            version(row.get("evaluation_schema_version")),
            row.get("status") != "partial",
            row.get("registered_at", ""),
            row["_id"],
        )

    for doc in documents:
        key = (doc["run_id"], doc["trial_id"])
        if key not in by_trial or order(doc) > order(by_trial[key]):
            by_trial[key] = doc
    return sorted(by_trial.values(), key=lambda d: (d["run_id"], d["trial_id"] or ""))


class LocalCatalog:
    def __init__(self, path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, document TEXT, PRIMARY KEY(kind,id))"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS run_records ON records(kind,json_extract(document,'$.run_id'))"
        )

    def register(self, docs):
        with self.db:
            for table, doc in docs:
                document_schema(table).validate(doc)
                self.db.execute(
                    "INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO NOTHING",
                    (table, doc["_id"], canonical(doc).decode()),
                )

    def find(self, table, **criteria):
        rows = self.db.execute(
            "SELECT document FROM records WHERE kind=? ORDER BY id", (table,)
        )
        documents = (json.loads(row[0]) for row in rows)
        return [
            doc
            for doc in documents
            if all(doc.get(k) == v for k, v in criteria.items())
        ]


class MongoCatalog:
    def __init__(self, db):
        self.db = db

    def setup(self):
        for name in ("runs", "trials", "snapshots"):
            self.db["lsb_" + name].create_index("experiment_id")
        self.db.lsb_trials.create_index([("run_id", 1), ("protocol_id", 1)])
        self.db.lsb_snapshots.create_index([("run_id", 1), ("trial_id", 1)])
        self.db.lsb_snapshots.create_index(
            [("protocol_id", 1), ("model", 1), ("executor", 1), ("harness_version", 1)]
        )
        self.db.lsb_snapshots.create_index([("status", 1), ("archive_status", 1)])
        self.db.lsb_source_bundles.create_index("protocol_id")

    def register(self, docs):
        # Replay after a failed write is safe. The snapshot is the commit marker
        # for a searchable and retrievable trial revision and is always written last.
        for table, doc in docs:
            document_schema(table).validate(doc)
            self.db["lsb_" + table].update_one(
                {"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True
            )

    def find(self, table, **criteria):
        return list(self.db["lsb_" + table].find(criteria).sort("_id", 1))


def configured_catalog(state, cloud):
    if not cloud:
        return LocalCatalog(state / "catalog.sqlite")
    from pymongo import MongoClient

    client = MongoClient(
        os.environ["MONGODB_URI"],
        appname="libstruct-archive",
        serverSelectionTimeoutMS=15000,
    )
    return MongoCatalog(client[os.environ.get("LSB_MONGO_DATABASE", "proofread")])


def index_documents(manifest, ref, locations):
    run = manifest["run_id"]
    trial = manifest["trial_id"]
    experiment = manifest["experiment_id"]
    bundle = manifest["source_bundle_id"]
    protocol = manifest["protocol_id"]
    parent = {"experiment_id": experiment, "run_id": run}
    source_keys = {
        a["path"].removeprefix("protocols/"): a.get("key")
        for a in manifest["artifacts"]
        if a["role"] == "source"
    }
    documents = [
        {**d, "key": source_keys.get(d["path"])}
        for d in manifest["source_bundle"]["documents"]
    ]
    return [
        ("experiments", {"_id": experiment, "project": "libstructbench"}),
        (
            "runs",
            {
                "_id": run,
                "experiment_id": experiment,
                "job_id": manifest["job_id"],
                "model": manifest["model"],
                "executor": manifest["executor"],
            },
        ),
        (
            "source_bundles",
            {
                "_id": bundle,
                "protocol_id": protocol,
                "key": manifest["source_manifest_key"],
                "documents": documents,
                "location": locations["evidence"],
                "status": manifest["source_bundle"]["status"],
            },
        ),
        (
            "trials",
            {
                "_id": trial,
                **parent,
                "protocol_id": protocol,
                "execution_id": manifest["execution_id"],
            },
        ),
        (
            "snapshots",
            {
                "_id": ref["sha256"],
                **parent,
                **ref,
                "trial_id": trial,
                "protocol_id": protocol,
                "source_bundle_id": bundle,
                "source_status": manifest["source_bundle"]["status"],
                "source_path": manifest["path"],
                "technology_id": manifest["technology_id"],
                "locations": locations,
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "checkpoint_refs": manifest["checkpoints"],
                "status": manifest["status"],
                "archive_status": "verified-with-exclusions"
                if any(a["status"] == "withheld" for a in manifest["artifacts"])
                else "verified",
                **manifest["metadata"],
                "model": manifest["model"],
                "executor": manifest["executor"],
            },
        ),
    ]
