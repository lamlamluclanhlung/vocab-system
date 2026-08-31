"""The official operational runtime composition root frozen by D70.

This module never imports the concrete event journal module. It obtains a
journal only through ``vocab.runtime.eventlog_authority``, and only inside a
command that holds the deployment lock.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sys as _sys
import uuid

from .anki import AnkiConnectClient
from .forge import forge
from .forge.request import ForgeRequest, ForgeStatus
from .runtime import forge_bridge
from .runtime.bootstrap import create_deployment, evaluate_preconditions
from .runtime.config import RuntimeConfig, load_config
from .runtime.corpus_runner import run_corpus_scan
from .runtime.errors import (
    RuntimeAssessmentError,
    RuntimeAttemptError,
    RuntimeForgeBridgeError,
    RuntimeLockError,
    RuntimeSemanticBridgeError,
    RuntimeSessionPlanError,
    VocabRuntimeError,
)
from .runtime.eventlog_authority import open_runtime_event_log
from .runtime.layout import build_layout
from .runtime.normalize import FILESYSTEM_SEAM, normalized
from .runtime.operation import write_operation
from .runtime.preflight import run_standalone_preflight
from .runtime.reconcile_runner import run_reconcile
from .runtime import assessment_session, attempt_runner, semantic_bridge
from .runtime.artifact_store_gate import open_deployment_artifact_store
from .runtime.session_plan import parse_session_plan
from .runtime.targets import resolve_targets


EXIT_SUCCESS = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_LOCK_CONTENTION = 3
EXIT_ITEM_FAILURES = 4


def _build_anki(config: RuntimeConfig) -> AnkiConnectClient:
    return AnkiConnectClient(
        endpoint=config.anki.endpoint,
        timeout=config.anki.timeout,
    )


def _command_preflight(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    layout = build_layout(config.data_root)
    report = run_standalone_preflight(config, layout, _build_anki(config))
    print(f"deployment: {config.data_root}")
    print(report.render())
    if not report.ok:
        print(f"\npreflight FAILED: {len(report.failed)} check(s)")
        return EXIT_REFUSED
    print("\npreflight OK (diagnostic only; not authority to write)")
    return EXIT_SUCCESS


def _command_bootstrap(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)
    preconditions = evaluate_preconditions(
        config,
        anki,
        confirm_new_deployment=bool(arguments.confirm_new_deployment),
        confirm_clean_production_profile=bool(
            arguments.confirm_clean_production_profile
        ),
    )

    print(f"target data root: {config.data_root}")
    print(
        f"\nProduction profile registry — "
        f"{preconditions.registry_count} VocabularyUnit note(s):\n"
    )
    if preconditions.registry:
        width = max(len(entry.unit_key) for entry in preconditions.registry)
        for entry in preconditions.registry:
            print(f"  {entry.unit_key.ljust(width)}  {entry.lemma}")
    else:
        print("  (none)")
    print(f"\nregistry digest: {preconditions.registry_digest}")

    if not preconditions.confirmations_present:
        print(
            "\nThis registry will become the production registry. Nothing has "
            "been created.\nRe-run with --confirm-new-deployment and "
            "--confirm-clean-production-profile to proceed."
        )
        return EXIT_REFUSED

    result = create_deployment(preconditions)
    print(f"\nbootstrapped deployment {result.identity.runtime_id}")
    print(f"namespace durability (D-6): {result.namespace_durable}")
    return EXIT_SUCCESS


def _read_artifact(
    path: Path,
    label: str,
    error_type: type[VocabRuntimeError] = RuntimeForgeBridgeError,
) -> bytes:
    """Read one transport file, owned by the caller's own error family.

    Ownership is passed explicitly rather than inferred from the filename, so a
    Wave C plan or proposal never surfaces as a Forge bridge failure.
    """
    with normalized(
        error_type, f"{label} could not be read", catching=FILESYSTEM_SEAM
    ):
        return path.read_bytes()


def _command_forge_export(arguments: argparse.Namespace) -> int:
    request = ForgeRequest(
        source_ref=arguments.source_ref,
        source_sentence=arguments.source_sentence,
        learner_note=arguments.learner_note,
    )
    prompt = forge_bridge.load_prompt()
    body = forge_bridge.build_request_artifact(request, prompt)

    out = Path(arguments.out)
    with normalized(
        RuntimeForgeBridgeError,
        "request artifact could not be written",
        catching=FILESYSTEM_SEAM,
    ):
        # Exclusive creation: never silently replace a human's existing work.
        with out.open("xb") as handle:
            handle.write(body)

    print(f"wrote {out}")
    print(f"generation_request_sha256 {forge_bridge.generation_request_sha256(request)}")
    print(f"prompt {prompt.version} {prompt.sha256}")
    return EXIT_SUCCESS


def _command_forge_import(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    # D70 section 11: write authority is established first. Reading, parsing,
    # and binding the artifacts is operation-specific work, so it may only run
    # once identity, the lock, and the full write-preflight have all passed.
    with write_operation(config, anki) as layout:
        prompt = forge_bridge.load_prompt()
        request_artifact = forge_bridge.parse_request_artifact(
            _read_artifact(Path(arguments.request), "request artifact")
        )
        response_artifact = forge_bridge.parse_response_artifact(
            _read_artifact(Path(arguments.response), "response artifact")
        )
        bound = forge_bridge.bind_generation(
            request_artifact, response_artifact, prompt
        )
        generator = forge_bridge.ReplayGenerator(bound)
        confirmation = forge_bridge.TerminalConfirmation(
            arguments.actor_id,
            stream_in=_sys.stdin,
            stream_out=_sys.stdout,
        )

        journal = open_runtime_event_log(layout.event_log_path)
        result = forge(
            bound.request,
            deck_name=config.anki.deck_name,
            generator=generator,
            anki=anki,
            event_log=journal,
            confirmation=confirmation,
            generation_metadata=bound.metadata,
            today=forge_bridge.local_day,
            attempt_id_factory=lambda: uuid.uuid4().hex,
        )

    line = f"{result.status.value}  unit_key={result.unit_key or '-'}"
    if result.outcome:
        line += f"  outcome={result.outcome}"
    if result.note_id is not None:
        line += f"  note={result.note_id}"
    if result.violations:
        line += f"  violations={','.join(result.violations)}"
    print(line)
    return (
        EXIT_SUCCESS
        if result.status is ForgeStatus.CREATED
        else EXIT_ITEM_FAILURES
    )


def _command_reconcile(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    with write_operation(config, anki) as layout:
        targets = resolve_targets(anki, unit_key=arguments.unit_key)
        journal = open_runtime_event_log(layout.event_log_path)
        failed = run_reconcile(
            targets,
            anki=anki,
            event_log=journal,
            stream_out=_sys.stdout,
        )
    return EXIT_SUCCESS if failed == 0 else EXIT_ITEM_FAILURES


def _command_corpus_scan(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    with write_operation(config, anki) as layout:
        journal = open_runtime_event_log(layout.event_log_path)
        run_corpus_scan(
            config,
            source=arguments.source,
            month=arguments.month,
            anki=anki,
            event_log=journal,
            stream_out=_sys.stdout,
        )
    return EXIT_SUCCESS


class _TerminalAttemptPort:
    """The production display and interaction boundary for one attempt.

    Only the exact stream calls are wrapped. A failing terminal after a durable
    reservation is an operational refusal: the reservation stands, nothing is
    captured, and nothing is redisplayed.
    """

    def __init__(self, stream_in, stream_out) -> None:
        self._in = stream_in
        self._out = stream_out

    def display_stimulus(self, payload: bytes) -> None:
        # Exact bytes, with no added newline and no operator text mixed in.
        buffer = getattr(self._out, "buffer", None)
        with normalized(
            RuntimeAttemptError,
            "stimulus could not be displayed",
            catching=FILESYSTEM_SEAM,
        ):
            if buffer is None:
                self._out.write(payload.decode("utf-8"))
                self._out.flush()
            else:
                self._out.flush()
                buffer.write(payload)
                buffer.flush()

    def ask_terminal_action(self) -> str:
        with normalized(
            RuntimeAttemptError,
            "terminal action could not be collected",
            catching=FILESYSTEM_SEAM,
        ):
            self._out.write(
                "\n\n--- action required: SUBMIT / SKIP / REFUSE ---\n> "
            )
            self._out.flush()
            answer = self._in.readline()
        return answer.strip().upper()


def _command_session_create(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    # D70 section 11: reading and parsing the plan is operation-specific work,
    # so it may only run once identity, the lock, and the full write-preflight
    # have all passed.
    with write_operation(config, anki) as layout:
        plan = parse_session_plan(
            _read_artifact(
                Path(arguments.plan), "session plan", RuntimeSessionPlanError
            )
        )
        store = open_deployment_artifact_store(layout)
        result = assessment_session.create_session(
            plan,
            anki=anki,
            artifact_store=store,
            session_root=layout.session_root,
        )
    print(f"session_id  {result.session_id}")
    print(f"created_at  {result.created_at}")
    print(f"items       {result.item_count}")
    return EXIT_SUCCESS


def _command_attempt_run(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)
    response_file = (
        Path(arguments.response_file) if arguments.response_file else None
    )

    with write_operation(config, anki) as layout:
        store = open_deployment_artifact_store(layout)
        outcome = attempt_runner.run_fresh_attempt(
            layout,
            session_id=arguments.session_id,
            item_ordinal=arguments.item_ordinal,
            artifact_store=store,
            port=_TerminalAttemptPort(_sys.stdin, _sys.stdout),
            response_file=response_file,
        )
    print(
        f"\nattempt {outcome.attempt_id}  {outcome.unit_key}  "
        f"{outcome.channel}  {outcome.action}  {outcome.receipt_kind}"
    )
    return EXIT_SUCCESS


def _command_semantic_export(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)

    with write_operation(config, anki) as layout:
        store = open_deployment_artifact_store(layout)
        result = semantic_bridge.export_semantic_request(
            layout,
            session_id=arguments.session_id,
            item_ordinal=arguments.item_ordinal,
            artifact_store=store,
            anki=anki,
        )
        if arguments.out:
            out = Path(arguments.out)
            with normalized(
                RuntimeSemanticBridgeError,
                "transport copy could not be written",
                catching=FILESYSTEM_SEAM,
            ):
                with out.open("xb") as handle:
                    handle.write(result.canonical_bytes)
    print(f"attempt_id      {result.attempt_id}")
    print(f"request_ref     {result.request_ref}")
    print(f"request_digest  {result.request_digest}")
    return EXIT_SUCCESS


def _command_assess(arguments: argparse.Namespace) -> int:
    config = load_config(Path(arguments.config))
    anki = _build_anki(config)
    proposal = Path(arguments.proposal) if arguments.proposal else None

    with write_operation(config, anki) as layout:
        store = open_deployment_artifact_store(layout)
        # The journal is acquired lazily, at the emission boundary only, so a
        # rejected request, proposal, or plan never reaches the authority.
        common = {
            "session_id": arguments.session_id,
            "item_ordinal": arguments.item_ordinal,
            "artifact_store": store,
            "anki": anki,
            "open_event_log": lambda: open_runtime_event_log(
                layout.event_log_path
            ),
        }
        if arguments.path == "policy":
            result = semantic_bridge.emit_policy_assessment(layout, **common)
        elif arguments.path == "omitted":
            result = semantic_bridge.emit_omitted_assessment(layout, **common)
        else:
            if proposal is None:
                raise RuntimeAssessmentError(
                    "the semantic path requires --proposal"
                )
            result = semantic_bridge.emit_semantic_assessment(
                layout,
                request_ref=arguments.request_ref,
                proposal_bytes=_read_artifact(
                    proposal, "semantic proposal", RuntimeSemanticBridgeError
                ),
                assessor_id=arguments.assessor_id,
                assessor_version=arguments.assessor_version,
                reviewer_id=arguments.reviewer_id,
                reviewer_version=arguments.reviewer_version,
                decision=arguments.decision,
                **common,
            )
    print(
        f"{result.path}  attempt={result.attempt_id}  "
        f"unit={result.unit_key}  channel={result.channel}  "
        f"appended={result.appended}"
    )
    return EXIT_SUCCESS


def build_parser() -> argparse.ArgumentParser:
    """Build the frozen Wave A command surface."""
    parser = argparse.ArgumentParser(
        prog="vocab",
        description="Operational runtime for the vocabulary system (D70).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="create one new deployment data root",
    )
    bootstrap.add_argument("--config", required=True)
    bootstrap.add_argument(
        "--confirm-new-deployment",
        action="store_true",
        dest="confirm_new_deployment",
    )
    bootstrap.add_argument(
        "--confirm-clean-production-profile",
        action="store_true",
        dest="confirm_clean_production_profile",
    )
    bootstrap.set_defaults(handler=_command_bootstrap)

    preflight = subparsers.add_parser(
        "preflight",
        help="diagnose one deployment without locking or writing",
    )
    preflight.add_argument("--config", required=True)
    preflight.set_defaults(handler=_command_preflight)

    forge_export = subparsers.add_parser(
        "forge-export",
        help="write one Forge request artifact for manual generation",
    )
    forge_export.add_argument("--source-ref", required=True, dest="source_ref")
    forge_export.add_argument(
        "--source-sentence", required=True, dest="source_sentence"
    )
    forge_export.add_argument("--learner-note", default="", dest="learner_note")
    forge_export.add_argument("--out", required=True)
    forge_export.set_defaults(handler=_command_forge_export)

    forge_import = subparsers.add_parser(
        "forge-import",
        help="replay one saved generation through the Forge core",
    )
    forge_import.add_argument("--config", required=True)
    forge_import.add_argument("--request", required=True)
    forge_import.add_argument("--response", required=True)
    forge_import.add_argument("--actor-id", required=True, dest="actor_id")
    forge_import.set_defaults(handler=_command_forge_import)

    reconcile = subparsers.add_parser(
        "reconcile",
        help="materialize T9 lifecycle transitions from Anki review history",
    )
    reconcile.add_argument("--config", required=True)
    selection = reconcile.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--unit-key", dest="unit_key")
    reconcile.set_defaults(handler=_command_reconcile)

    corpus_scan = subparsers.add_parser(
        "corpus-scan",
        help="count one corpus month and emit its ENCOUNTER events",
    )
    corpus_scan.add_argument("--config", required=True)
    corpus_scan.add_argument("--source", required=True)
    corpus_scan.add_argument("--month", required=True)
    corpus_scan.set_defaults(handler=_command_corpus_scan)

    session_create = subparsers.add_parser(
        "session-create",
        help="publish one human-authored R/W assessment session",
    )
    session_create.add_argument("--config", required=True)
    session_create.add_argument("--plan", required=True)
    session_create.set_defaults(handler=_command_session_create)

    attempt_run = subparsers.add_parser(
        "attempt-run",
        help="run one fresh assessment attempt in this process",
    )
    attempt_run.add_argument("--config", required=True)
    attempt_run.add_argument("--session-id", required=True, dest="session_id")
    attempt_run.add_argument(
        "--item-ordinal", required=True, type=int, dest="item_ordinal"
    )
    attempt_run.add_argument("--response-file", dest="response_file")
    attempt_run.set_defaults(handler=_command_attempt_run)

    semantic_export = subparsers.add_parser(
        "semantic-export",
        help="store one attempt-bound canonical T11 semantic request",
    )
    semantic_export.add_argument("--config", required=True)
    semantic_export.add_argument("--session-id", required=True, dest="session_id")
    semantic_export.add_argument(
        "--item-ordinal", required=True, type=int, dest="item_ordinal"
    )
    semantic_export.add_argument("--out")
    semantic_export.set_defaults(handler=_command_semantic_export)

    assess = subparsers.add_parser(
        "assess",
        help="emit the final T12 JUDGE for one attempt",
    )
    assess.add_argument("--config", required=True)
    assess.add_argument("--session-id", required=True, dest="session_id")
    assess.add_argument(
        "--item-ordinal", required=True, type=int, dest="item_ordinal"
    )
    assess.add_argument(
        "--path", required=True, choices=("policy", "omitted", "semantic")
    )
    assess.add_argument("--request-ref", dest="request_ref")
    assess.add_argument("--proposal")
    assess.add_argument("--assessor-id", dest="assessor_id")
    assess.add_argument("--assessor-version", dest="assessor_version")
    assess.add_argument("--reviewer-id", dest="reviewer_id")
    assess.add_argument("--reviewer-version", type=int, dest="reviewer_version")
    assess.add_argument("--decision")
    assess.set_defaults(handler=_command_assess)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command, mapping every refusal onto a frozen exit code.

    Only the runtime exception taxonomy is caught. Known operational failures
    from Anki, storage, and the core are normalized into that taxonomy at the
    runtime boundary. Anything outside it is a programming defect and is
    allowed to surface with a traceback, because a defect reported as exit 1
    would be indistinguishable from a deliberate fail-closed refusal.
    """
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except RuntimeLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_LOCK_CONTENTION
    except VocabRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
