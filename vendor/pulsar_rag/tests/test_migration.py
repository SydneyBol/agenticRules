"""The migration against an in-memory S3 and lambda: dry run changes nothing; apply backs up before it deletes and
invokes the rebuild; verify compares with the backup; the fine-tune clean drops disabled rows only."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "migrations"))
import rebuild_rag_store as m  # noqa: E402


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    class exceptions:
        ClientError = ClientError

    def __init__(self, objects):
        self.objects, self.calls = dict(objects), []

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError("404")

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body):
        self.calls.append(("put", Key))
        self.objects[(Bucket, Key)] = Body

    def copy_object(self, Bucket, CopySource, Key):
        self.calls.append(("copy", CopySource["Key"], Key))
        self.objects[(Bucket, Key)] = self.objects[(CopySource["Bucket"], CopySource["Key"])]

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Key))
        del self.objects[(Bucket, Key)]

    def get_paginator(self, _):
        objects = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for b, k in objects if b == Bucket and k.startswith(Prefix)]}]
        return P()


class FakeLambda:
    def __init__(self):
        self.events = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.events.append((FunctionName, InvocationType, json.loads(Payload)))


B, P = "gravty-ai-test", {"program_id": 7, "schema": "t7", "rds_schema": "t7r"}
CSV = b"user,system\ngold spend,\"Filter_1, offer criteria\"\nrefer,rule_2 give points\n"


@pytest.fixture
def s3():
    return FakeS3({(B, "7/gpt/rag/offer/vectordb/data.csv"): CSV, (B, "7/gpt/rag/mechanism.json"): b"{}",
                   (B, "7/gpt/offer"): b"\x80\x04pickled"})


def test_dry_run_changes_nothing(s3):
    lam = FakeLambda()
    out = m.migrate_program(s3, lam, B, P, "T", "data-store", apply=False, log=lambda *_: None)
    assert s3.calls == [] and lam.events == [] and out["backed_up"] == [] and not out["deleted_index"]


def test_apply_backs_up_before_deleting_and_invokes_rebuild(s3):
    lam = FakeLambda()
    out = m.migrate_program(s3, lam, B, P, "T", "data-store", apply=True, log=lambda *_: None)
    copies = [c for c in s3.calls if c[0] == "copy"]
    assert [c[2] for c in copies] == ["7/gpt/backup/rag-migration-T/rag/offer/vectordb/data.csv",
                                      "7/gpt/backup/rag-migration-T/rag/mechanism.json",
                                      "7/gpt/backup/rag-migration-T/offer"]
    assert s3.calls.index(("delete", "7/gpt/offer")) > s3.calls.index(copies[-1])
    assert (B, "7/gpt/offer") not in s3.objects and (B, "7/gpt/rag/offer/vectordb/data.csv") in s3.objects
    assert lam.events == [("data-store", "Event", {"program_id": "7", "schema": "t7", "rds_schema": "t7r",
                                                    "api_trigger": True})]
    assert out["deleted_index"] and out["invoked"]


def test_since_widens_the_selection():
    event = m.lambda_event(P, since="2025-01-01", redshift_db="dev")
    assert "dev.t7.offers_offer" in event["query"] and "updated_ts >= '2025-01-01'" in event["query"]


def test_verify_compares_with_latest_backup(s3):
    m.migrate_program(s3, FakeLambda(), B, P, "T", "data-store", apply=True, log=lambda *_: None)
    s3.objects[(B, "7/gpt/rag/offer/vectordb/data.csv")] = b"user,system\ngold spend,x\nbirthday,y\nweekend,z\n"
    res = m.verify_program(s3, B, 7, log=lambda *_: None)
    assert (res["before"], res["after"], res["removed"], res["added"]) == (2, 3, 1, 2)


def test_twice_typed_pattern():
    assert m.TWICE_TYPED.search('if member offer_level_tracker "Total NUMBER spend" > 1')
    assert m.TWICE_TYPED.search('"NUMBER Total NUMBER spend"')
    assert not m.TWICE_TYPED.search('if member offer_level_tracker "NUMBER Total spend" > 1')


def test_clean_finetune_drops_disabled_rows_only():
    body = ('SubPrompt,Block\n"give_points 5","<block type=""action"" disabled-reasons=""MANUALLY_DISABLED"">"\n'
            '"if member tier","<block type=""if_generic_adaptive"">"\n'
            '"update x","<block type=""action"" disabled=""true"">"\n').encode()
    s3 = FakeS3({(m.FINETUNE_BUCKET, m.FINETUNE_KEY): body})
    assert m.clean_finetune(s3, "T", apply=False, log=lambda *_: None) == {"rows": 3, "dropped": 2, "twice_typed": 0}
    assert s3.calls == []
    m.clean_finetune(s3, "T", apply=True, log=lambda *_: None)
    kept = m.read_rows(s3, m.FINETUNE_BUCKET, m.FINETUNE_KEY)
    assert [r["SubPrompt"] for r in kept] == ["if member tier"]
    assert (m.FINETUNE_BUCKET, "blockwise_subprompt/backup/retrain_data-T.csv") in s3.objects
