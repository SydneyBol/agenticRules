"""The runtime settings, read once by an entry point (api/service.py, api/cli.py) and handed down to agent.run.
Nothing else reads the environment: library code takes Settings() (the code defaults) unless it is given these."""
import os
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo


@dataclass(frozen=True)
class Settings:
    max_turns: int = 14  # model turns per run
    early_tools: bool = True  # start each tool call as soon as it has streamed
    compile_url: str = "http://127.0.0.1:8078"  # the stock-screen compile service (harness/ui/compile)
    engine_jar: str = os.path.join(ROOT, "harness", "engine", "target", "harness.jar")
    engine_warm: bool = True  # one long-lived engine harness (--serve) instead of a JVM per run

    @classmethod
    def from_env(cls, env=None):
        """OFFER_AUTHOR_TURNS, _EARLY_TOOLS (0 = off), _COMPILE_URL, _ENGINE_JAR, _ENGINE_WARM (0 = off)."""
        env, d = os.environ if env is None else env, cls()
        return cls(max_turns=int(env.get("OFFER_AUTHOR_TURNS", d.max_turns)),
                   early_tools=env.get("OFFER_AUTHOR_EARLY_TOOLS", "1") != "0",
                   compile_url=env.get("OFFER_AUTHOR_COMPILE_URL", d.compile_url),
                   engine_jar=env.get("OFFER_AUTHOR_ENGINE_JAR", d.engine_jar),
                   engine_warm=env.get("OFFER_AUTHOR_ENGINE_WARM", "1") != "0")
