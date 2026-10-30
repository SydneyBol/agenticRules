"""offer-xml CLI with a scripted model: single prompt to a file, stdout, batch, exit codes."""

import json
import logging
import os

from offer_author.ai.model import LLMError
from offer_author.api.cli import main
from tests import paths
from tests.fakes import ScriptedLLM
from tests.support import finish, golden

FIXTURE = ["--catalog", paths.FIXTURE]
EARN = ("compose", golden("S03_combined_weekend_gold_locations"))


def earn_turn():
    return [EARN, finish(readback="2x points at the downtown store")]


def test_prompt_to_file_writes_only_the_xml_and_logs_the_notes(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="offer_author.cli")
    out = tmp_path / "downtown.xml"
    rc = main(["2x points at the downtown store", "-o", str(out), *FIXTURE], llm=ScriptedLLM([earn_turn()]))
    assert rc == 0 and os.listdir(tmp_path) == ["downtown.xml"]
    xml = out.read_text("ascii")
    assert xml.startswith("<xml") and 'type="offer_generic"' in xml and 'value="L1,L2"' in xml and "<comment" not in xml
    msg = caplog.records[-1].getMessage()
    notes = json.loads(msg[msg.index("{") :])
    assert notes["status"] == "draft" and notes["readback"] == "2x points at the downtown store"


def test_stdout(capsys):
    assert main(["2x at downtown", *FIXTURE], llm=ScriptedLLM([earn_turn()])) == 0
    assert 'type="offer_generic"' in capsys.readouterr().out


def test_batch_names_files_from_the_prompt_and_reports_worst_exit(tmp_path):
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("# comment\n2x points at the downtown store\n\nsomething the model fails on\n")
    rc = main(
        ["--batch", str(prompts), "--out-dir", str(tmp_path / "out"), *FIXTURE],
        llm=ScriptedLLM([earn_turn(), LLMError("ThrottlingException")]),
    )
    assert rc == 1
    assert os.listdir(tmp_path / "out") == ["01-2x-points-at-the-downtown-store.xml"]
