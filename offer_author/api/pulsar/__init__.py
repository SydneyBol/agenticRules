"""The Pulsar-compatible API: the stock gravty-ui's GenAI endpoints, answered by the offer author.

routes.py   the endpoints (HTTP in and out)
runs.py     which agent run answers a screen's prompt
jobs.py     runs in the background, kept for the screens that ask again
payloads.py Pulsar's answers, and what each run says about a part (for the log)
"""

from .jobs import Job, JobStore
from .payloads import ascii_xml, error_message, notes, offer_main_metadata, part_view, xml_answer
from .routes import pulsar_blueprint

__all__ = [
    "Job",
    "JobStore",
    "ascii_xml",
    "error_message",
    "notes",
    "offer_main_metadata",
    "part_view",
    "pulsar_blueprint",
    "xml_answer",
]
