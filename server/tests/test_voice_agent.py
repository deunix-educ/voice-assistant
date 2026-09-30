"""Tests de l'agent Linux, étape 15b : configuration, liste blanche, exécution (sans broker)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from voice_agent import Action, AgentConfig, ConfigError, load_config, run_action, state_payload  # noqa: E402


def config(**actions: Action) -> AgentConfig:
    return AgentConfig(agent_id="pc-test", host="127.0.0.1", port=1883, username=None, password=None,
                       topic_prefix="agent", dry_run=False, actions=dict(actions))


def test_example_config_is_valid() -> None:
    loaded = load_config(AGENT_DIR / "agent.yaml.example")
    assert loaded.agent_id == "pc-bureau" and not loaded.dry_run
    assert loaded.actions["shutdown"].command == ("systemctl", "poweroff")
    assert loaded.topic("command") == "agent/pc-bureau/command"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("id: PC Bureau\nmqtt: {host: x}\n", "id « PC Bureau » invalide"),
        ("id: pc\nmqtt: {}\n", "mqtt.host manquant"),
        # Une chaîne passerait par un shell : « poweroff; rm -rf / » serait exécutable.
        ("id: pc\nmqtt: {host: x}\nactions: {off: {command: 'systemctl poweroff'}}\n", "doit être une liste"),
        ("id: pc\nmqtt: {host: x}\nactions: {off: {command: [x], timeout_s: 0}}\n", "timeout_s"),
    ],
)
def test_invalid_config_is_explained(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_only_listed_actions_run() -> None:
    result = run_action(config(ok=Action(("true",), 5)), "rm", "r1")
    assert not result.ok and result.output == "action inconnue : rm"


def test_success_failure_and_output() -> None:
    agent = config(ok=Action(("echo", "bonjour"), 5), ko=Action(("false",), 5))
    ok = run_action(agent, "ok", "r1")
    assert ok.ok and ok.code == 0 and ok.output == "bonjour"
    ko = run_action(agent, "ko", "r2")
    assert not ko.ok and ko.code == 1


def test_missing_program_and_timeout() -> None:
    agent = config(absent=Action(("/nonexistent/prog",), 5), slow=Action(("sleep", "5"), 0.2))
    assert run_action(agent, "absent", "r").output == "programme introuvable : /nonexistent/prog"
    slow = run_action(agent, "slow", "r")
    assert not slow.ok and slow.output == "arretee apres 0.2 s"


def test_dry_run_executes_nothing() -> None:
    calls: list[object] = []
    agent = AgentConfig(**{**config(off=Action(("systemctl", "poweroff"), 5)).__dict__, "dry_run": True})
    result = run_action(agent, "off", "r", runner=lambda *args, **kwargs: calls.append(args))  # type: ignore[arg-type]
    assert result.ok and result.output == "essai : systemctl poweroff" and calls == []


def test_state_lists_actions_for_the_server() -> None:
    online = json.loads(state_payload(config(b=Action(("x",), 1), a=Action(("y",), 1)), True))
    assert online == {"status": "online", "actions": ["a", "b"], "dry_run": False}
    assert json.loads(state_payload(config(), False)) == {"status": "offline"}


def test_result_payload() -> None:
    result = run_action(config(ok=Action(("true",), 5)), "ok", "w42")
    assert json.loads(result.payload()) == {"action": "ok", "id": "w42", "ok": True, "code": 0, "output": ""}
