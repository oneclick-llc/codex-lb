"""The HTTP probes must budget for a single-event-loop replica.

A replica runs one asyncio event loop, so a probe request is scheduled behind
whatever the loop is already running. Kubernetes' 1s default `timeoutSeconds`
turns ordinary loop pressure into probe failures: a saturated loop dropped the
pod out of the Service endpoints while the process was still serving traffic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"
_KUBERNETES_DEFAULT_PROBE_TIMEOUT_SECONDS = 1


def _probe_block(template: str, probe: str) -> str:
    match = re.search(rf"^ +{probe}:\n((?: +\S.*\n)+)", template, re.MULTILINE)
    assert match is not None, f"{probe} not found in deployment template"
    return match.group(1)


def _probe_seconds(block: str, field: str) -> int | None:
    match = re.search(rf"^ +{field}: (\d+)$", block, re.MULTILINE)
    return None if match is None else int(match.group(1))


@pytest.mark.parametrize("probe", ["readinessProbe", "livenessProbe"])
def test_probe_timeout_exceeds_kubernetes_default(probe: str) -> None:
    template = (_CHART_DIR / "templates" / "deployment.yaml").read_text()
    block = _probe_block(template, probe)

    timeout_seconds = _probe_seconds(block, "timeoutSeconds")
    assert timeout_seconds is not None, f"{probe} relies on the {_KUBERNETES_DEFAULT_PROBE_TIMEOUT_SECONDS}s default"
    assert timeout_seconds > _KUBERNETES_DEFAULT_PROBE_TIMEOUT_SECONDS


@pytest.mark.parametrize("probe", ["readinessProbe", "livenessProbe"])
def test_probe_timeout_stays_within_its_failure_window(probe: str) -> None:
    """A timeout at or above period * threshold would stall detection."""
    template = (_CHART_DIR / "templates" / "deployment.yaml").read_text()
    block = _probe_block(template, probe)

    timeout_seconds = _probe_seconds(block, "timeoutSeconds")
    period_seconds = _probe_seconds(block, "periodSeconds")
    failure_threshold = _probe_seconds(block, "failureThreshold")
    assert timeout_seconds is not None
    assert period_seconds is not None
    assert failure_threshold is not None

    assert timeout_seconds < period_seconds * failure_threshold
