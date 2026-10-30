"""Rebuilds each program's Pulsar RAG store with the fixed pipeline, after backing up what is there; cleans the shared
fine-tune file. Dry run unless --apply. Deploy the fixed code (lambda, Batch image, API) before running with --apply.

Why a rebuild and not a row fix: the faulty pipeline wrote examples from disabled blocks (Blockly 10
disabled-reasons), counter names it failed to type ("Spend (AED)") or typed twice ("Total NUMBER spend"), offers whose
values were not templated (real stores and tiers), and it silently dropped offers whose templatised XML it broke. An
example row (prompt, sub-prompt lines) does not say which block a line came from, so the only correct store is one
built again from the offers' saved XML. The fine-tune file keeps each line's block XML, so its disabled rows can be
dropped in place.

Per program (--apply):
  1. back up rag/ (vectordb/data.csv, mechanism.json), data_store/offers.csv and the pickled index gpt/offer to
     gpt/backup/rag-migration-<UTC time>/ in the same bucket;
  2. delete the pickled index gpt/offer (never readable: the API decoded it as UTF-8 and rebuilt every time; the fixed
     API no longer reads or writes it);
  3. invoke the data_store lambda with api_trigger (launched reward offers of the last year, or --since): it collects
     each offer's saved XML and prompts and submits the validation Batch job, which (manual trigger) drops
     mechanism.json, builds vectordb/data.csv again with the fixed sub-prompts and templating, and calls
     /ai/pulsar/refresh-cache.
Then --verify compares each program's new data.csv with its latest backup.

  python rebuild_rag_store.py --env=prod --programs=programs.json [--apply] [--since=2025-01-01 --redshift-db=...]
  python rebuild_rag_store.py --env=prod --programs=programs.json --verify
  python rebuild_rag_store.py --env=prod --clean-finetune [--finetune-role-arn=...] [--apply]
programs.json: [{"program_id": 1281, "schema": "devintbf2", "rds_schema": "devintbf2"}, ...]
"""
import argparse
import csv
import io
import json
import re
from datetime import datetime, timezone

DISABLED = re.compile(r'disabled="true"|disabled-reasons="')
# a typed prefix inside a quoted name: the old replace hit a name inside a longer one ("Total NUMBER spend")
TWICE_TYPED = re.compile(r'"[^"]*\S\s(?:NUMBER|DATE|DATETIME|TEXT|LOOKUP|LOV|BOOLEAN)\s[^"]*"')
FINETUNE_BUCKET, FINETUNE_KEY = "gravty-ai-pulsar", "blockwise_subprompt/retrain_data.csv"


def rag_keys(pid):
    return [f"{pid}/gpt/rag/offer/vectordb/data.csv", f"{pid}/gpt/rag/mechanism.json",
            f"{pid}/gpt/data_store/offers.csv", f"{pid}/gpt/offer"]


def exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def read_rows(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(body)))


def lambda_event(p, since=None, redshift_db=None):
    event = {"program_id": str(p["program_id"]), "schema": p["schema"], "rds_schema": p.get("rds_schema", p["schema"]),
             "api_trigger": True}
    if since:  # the same selection as api_trigger, from an earlier date (the lambda runs `query` as given)
        event["query"] = (f"SELECT * FROM {redshift_db}.{p['schema']}.offers_offer WHERE updated_ts >= '{since}' "
                          "AND status IN ('launched', 'suspended') AND offer_type = 'reward';")
    return event


def migrate_program(s3, lam, bucket, p, stamp, lambda_name, apply, since=None, redshift_db=None, log=print):
    """-> {program_id, backed_up: [keys], deleted_index, invoked}"""
    pid, out = p["program_id"], {"program_id": p["program_id"], "backed_up": [], "deleted_index": False,
                                 "invoked": False}
    present = [k for k in rag_keys(pid) if exists(s3, bucket, k)]
    rows = len(read_rows(s3, bucket, rag_keys(pid)[0])) if rag_keys(pid)[0] in present else 0
    log(f"program {pid}: {rows} RAG examples; present: {[k.split('/gpt/')[1] for k in present]}")
    backup = f"{pid}/gpt/backup/rag-migration-{stamp}/"
    for key in present:
        log(f"  {'copy' if apply else 'would copy'} {key} -> {backup}{key.split('/gpt/')[1]}")
        if apply:
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=backup + key.split("/gpt/")[1])
            out["backed_up"].append(key)
    index_key = rag_keys(pid)[3]
    if index_key in present:
        log(f"  {'delete' if apply else 'would delete'} stale pickled index {index_key}")
        if apply:  # only after its backup above succeeded (copy_object raises otherwise)
            s3.delete_object(Bucket=bucket, Key=index_key)
            out["deleted_index"] = True
    event = lambda_event(p, since, redshift_db)
    log(f"  {'invoke' if apply else 'would invoke'} {lambda_name} {json.dumps(event)[:160]}")
    if apply:
        lam.invoke(FunctionName=lambda_name, InvocationType="Event", Payload=json.dumps(event).encode())
        out["invoked"] = True
    return out


def latest_backup(s3, bucket, pid):
    prefix = f"{pid}/gpt/backup/rag-migration-"
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
            for o in page.get("Contents", []) if o["Key"].endswith("rag/offer/vectordb/data.csv")]
    return max(keys) if keys else None


def verify_program(s3, bucket, pid, log=print):
    """New store vs the latest backup: example counts, prompts gone and added. -> {program_id, before, after, ...}"""
    new_key, old_key = rag_keys(pid)[0], latest_backup(s3, bucket, pid)
    new = read_rows(s3, bucket, new_key) if exists(s3, bucket, new_key) else []
    old = read_rows(s3, bucket, old_key) if old_key else []
    before, after = {r.get("user") for r in old}, {r.get("user") for r in new}
    res = {"program_id": pid, "before": len(old), "after": len(new), "removed": len(before - after),
           "added": len(after - before), "twice_typed_after": sum(bool(TWICE_TYPED.search(r.get("system") or ""))
                                                                  for r in new)}
    log(f"program {pid}: {res['before']} -> {res['after']} examples (removed {res['removed']}, added {res['added']}), "
        f"twice-typed names now {res['twice_typed_after']}")
    return res


def clean_finetune(s3, stamp, apply, log=print):
    """Drops fine-tune rows whose block was disabled; reports rows with a name typed twice (they need the rebuilt
    data: the block XML alone does not say what the name was). -> {rows, dropped, twice_typed}"""
    rows = read_rows(s3, FINETUNE_BUCKET, FINETUNE_KEY)
    keep = [r for r in rows if not DISABLED.search(r.get("Block") or "")]
    twice = [r for r in keep if TWICE_TYPED.search(r.get("SubPrompt") or "")]
    log(f"fine-tune: {len(rows)} rows, {len(rows) - len(keep)} from disabled blocks, {len(twice)} with a name typed twice")
    if apply and len(keep) < len(rows):
        backup = f"blockwise_subprompt/backup/retrain_data-{stamp}.csv"
        s3.copy_object(Bucket=FINETUNE_BUCKET, CopySource={"Bucket": FINETUNE_BUCKET, "Key": FINETUNE_KEY}, Key=backup)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(keep)
        s3.put_object(Bucket=FINETUNE_BUCKET, Key=FINETUNE_KEY, Body=buf.getvalue().encode("utf-8"))
        log(f"  backed up to {backup}, wrote {len(keep)} rows")
    return {"rows": len(rows), "dropped": len(rows) - len(keep), "twice_typed": len(twice)}


def main(argv=None):
    import boto3
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", required=True)
    ap.add_argument("--programs", help="JSON file: [{program_id, schema, rds_schema}]")
    ap.add_argument("--lambda-name", help="the deployed data_store lambda (pulsar/data_store/lambda_function.py)")
    ap.add_argument("--since", help="collect offers updated since this date instead of the last year")
    ap.add_argument("--redshift-db", help="Redshift database name, with --since")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--clean-finetune", action="store_true")
    ap.add_argument("--finetune-role-arn", help="role for the gravty-ai-pulsar bucket (the Batch job assumes one)")
    ap.add_argument("--apply", action="store_true", help="make the changes (default: print what would change)")
    a = ap.parse_args(argv)
    if a.since and not a.redshift_db:
        ap.error("--since needs --redshift-db")
    if a.programs and not a.verify and not a.lambda_name:
        ap.error("--programs needs --lambda-name (or --verify)")
    bucket, stamp = f"gravty-ai-{a.env}", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s3 = boto3.client("s3")
    if a.programs:
        with open(a.programs) as f:
            programs = json.load(f)
        if a.verify:
            for p in programs:
                verify_program(s3, bucket, p["program_id"])
        else:
            lam = boto3.client("lambda")
            for p in programs:
                migrate_program(s3, lam, bucket, p, stamp, a.lambda_name, a.apply, a.since, a.redshift_db)
    if a.clean_finetune:
        ft = s3
        if a.finetune_role_arn:
            c = boto3.client("sts").assume_role(RoleArn=a.finetune_role_arn, RoleSessionName="pulsar-rag-migration")
            c = c["Credentials"]
            ft = boto3.client("s3", aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                              aws_session_token=c["SessionToken"], region_name="us-west-2")
        clean_finetune(ft, stamp, a.apply)
    if not a.apply and not a.verify:
        print("dry run: nothing changed (add --apply)")


if __name__ == "__main__":
    main()
