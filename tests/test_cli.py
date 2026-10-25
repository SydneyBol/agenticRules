"""offer-xml CLI with a scripted model: single prompt to a file, stdout, batch, exit codes."""
import json
import os

from conftest import ROOT, c, finish
from offer_author.cli import main
from offer_author.llm import LLMError, ScriptedLLM

FIXTURE = ["--catalog", os.path.join(ROOT, "catalogs", "fixture.json")]


def earn_turn():
    return [("add_earn", {"measure": "h_bit_amount", "factor_kind": "multiplier", "factor": 2, "or_groups": [],
                          "conditions": [c("activity", "h_location", "in", values=["L1"])]}),
            finish(readback="2x points at the downtown store", activity_types=["SPEND"])]


def test_prompt_to_file_writes_xml_and_sidecar(tmp_path):
    out = tmp_path / "downtown.xml"
    rc = main(["2x points at the downtown store", "-o", str(out), *FIXTURE], llm=ScriptedLLM([earn_turn()]))
    assert rc == 0
    xml = out.read_text("ascii")
    assert xml.startswith("<xml") and 'type="offer_generic"' in xml and 'value="L1"' in xml
    assert '<comment pinned="true"' in xml and "AI draft: 2x points at the downtown store" in xml
    side = json.loads((tmp_path / "downtown.json").read_text())
    assert side["status"] == "draft" and side["rule_count"] == 1 and side["latency_s"] is not None


def test_plain_omits_the_note(tmp_path):
    out = tmp_path / "p.xml"
    main(["2x at downtown", "-o", str(out), "--plain", *FIXTURE], llm=ScriptedLLM([earn_turn()]))
    assert "<comment" not in out.read_text("ascii")


def test_stdout(capsys):
    assert main(["2x at downtown", *FIXTURE], llm=ScriptedLLM([earn_turn()])) == 0
    assert 'type="offer_generic"' in capsys.readouterr().out


def test_batch_names_files_from_the_prompt_and_reports_worst_exit(tmp_path):
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("# comment\n2x points at the downtown store\n\nsomething the model fails on\n")
    rc = main(["--batch", str(prompts), "--out-dir", str(tmp_path / "out"), *FIXTURE],
              llm=ScriptedLLM([earn_turn(), LLMError("ThrottlingException")]))
    assert rc == 1
    files = sorted(os.listdir(tmp_path / "out"))
    assert files == ["01-2x-points-at-the-downtown-store.json", "01-2x-points-at-the-downtown-store.xml",
                     "02-something-the-model-fails-on.json"]
    assert json.load(open(tmp_path / "out" / "02-something-the-model-fails-on.json"))["status"] == "error"
