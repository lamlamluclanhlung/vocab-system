"""Frozen D58/D69 invariant-probe registry."""

from __future__ import annotations

from vocab.contracts import T12_LIFECYCLE_ENABLED_CHANNELS


# This is the independent, frozen list of mandatory D58/D69 obligations.  It
# must remain a literal: the registry below supplies proofs for these names but
# has no authority to define or shrink the obligation set.
D58_CLAUSES = (
    "producer_text_exact_rerun_appends_zero",
    "producer_text_changed_payload_conflicts",
    "producer_duplicate_identical_judge_fails",
    "producer_arbitrary_extra_field_rejected",
    "producer_partial_d35_rejected",
    "producer_judge_requires_compatible_exposure",
    "producer_speak_only_resumes_judge_only",
    "producer_judge_only_speech_fails",
    "producer_duplicate_identical_speak_fails",
    "producer_changed_retranscription_conflicts",
    "producer_changed_rejudgment_conflicts",
    "producer_speech_pair_exact_rerun_appends_zero",
    "identity_l_voice_independent",
    "identity_d54_trivial_formatting_independent",
    "identity_genuine_cognitive_change_distinct",
    "identity_same_audio_changed_transcript_stable",
    "identity_same_attempt_rejudged_stable",
    "identity_attempt_stable_within_session",
    "novelty_prior_omitted_consumed",
    "novelty_prior_abstain_consumed",
    "novelty_interrupted_reservation_consumed",
    "exposure_crash_before_reservation_no_display",
    "exposure_crash_after_reservation_before_display_durable",
    "exposure_crash_after_display_before_outcome_durable",
    "exposure_restart_reservation_no_redisplay",
    "exposure_restart_capture_resumable_no_redisplay",
    "capture_orphan_artifact_is_inert",
    "speech_raw_stt_cannot_establish_omission",
    "speech_interruption_without_receipt_has_no_evidence",
    "speech_unverified_stt_cannot_enter_d19",
    "speech_confidence_threshold_cannot_establish_omission",
    "speech_uncertain_is_abstain",
    "speech_failed_is_abstain",
    "speech_human_approved_success_may_enter_d19",
    "speech_human_confirmed_absence_is_omitted",
    "speech_passed_tracks_semantic_pass",
    "speech_speak_carries_zero_d35",
    "semantic_anchor_R_correct_paraphrase_pass",
    "semantic_anchor_R_wrong_meaning_fail",
    "semantic_anchor_R_off_topic_abstain",
    "semantic_anchor_R_no_target_presence_gate",
    "semantic_anchor_L_correct_interpretation_pass",
    "semantic_anchor_L_wrong_interpretation_fail",
    "semantic_anchor_L_no_target_presence_gate",
    "semantic_anchor_W_correct_use_pass",
    "semantic_anchor_W_target_absent_omitted",
    "semantic_anchor_W_semantic_misuse_fail",
    "semantic_anchor_W_collocation_misuse_fail",
    "semantic_anchor_S_verified_correct_use_pass",
    "semantic_anchor_S_verified_misuse_fail",
    "semantic_anchor_S_unverified_omission_abstain",
    "semantic_anchor_S_transcription_uncertainty_abstain",
    "semantic_anchor_S_verified_absence_omitted",
    "semantic_anchor_reviewer_rejection_abstain",
    "static_eventlog_authority_real_tree",
    "static_eventlog_authority_mutations",
    "strict_read_equivalence",
    "strict_read_recovery_torn_tail",
    "strict_read_lifecycle_torn_tail",
    "strict_read_r1_r2_authority",
    "lifecycle_consumer_gate",
    "lifecycle_legacy_compatibility",
    "lifecycle_anti_downgrade",
    "frozen_envelope_v1_decodes_and_bears_lifecycle",
    "frozen_envelope_requires_v1_decoder",
    "state_materializer_surface",
    "state_materializer_indirect_authority",
    "concrete_events_import_allowlist",
    "production_eventlog_construction_p1a",
    "producer_crash_speak_append_failure",
    "producer_crash_durable_speak_return_failure",
    "producer_crash_judge_append_resume",
    "producer_crash_failed_confirmation",
    "pytest_inventory_closed_unskippable",
    "pytest_inventory_rejects_registry_mutations",
    "pytest_semantic_anchor_matrix_closed",
    "pytest_unskippability_rejects_mutations",
    "pytest_required_phases_must_pass",
    "pytest_direct_filters_cannot_certify",
    "pytest_hidden_filters_cannot_certify",
    "pytest_discovery_config_cannot_certify",
    "pytest_param_deselection_cannot_certify",
    "pytest_full_regression_failure_cannot_certify",
    "real_smoke_s1_text_pass",
    "real_smoke_s2_dispositions",
    "real_smoke_s3_speech_pair",
    "real_smoke_s4_exact_reruns",
)


# Independent fingerprints for every case in SEMANTIC_ANCHORS.  Task text is
# deliberately excluded: these fields uniquely identify the frozen semantic
# outcomes while keeping this inventory independent from the parametrized data.
D58_SEMANTIC_ANCHOR_CASES = (
    ("R", "PASS", "", "", False),
    ("R", "FAIL", "wrong_meaning", "", False),
    ("R", "ABSTAIN", "", "off_topic", False),
    ("L", "PASS", "", "", False),
    ("L", "FAIL", "wrong_interpretation", "", False),
    ("W", "PASS", "", "", True),
    ("W", "FAIL", "semantic_misuse", "", True),
    ("W", "FAIL", "collocation_misuse", "", True),
    ("S", "PASS", "", "", True),
    ("S", "FAIL", "semantic_misuse", "", True),
)


# Each clause names one normative obligation. Reusing a strong base selector
# across clauses does not duplicate execution because REQUIRED_D58_SELECTORS
# is a set; it makes the obligation-to-proof binding explicit and auditable.
D58_PROBE_INVENTORY = {
    # D58 producer-history obligations.
    "producer_text_exact_rerun_appends_zero": (
        "tests/test_t12_producer.py::test_text_missing_then_exact_rerun",
    ),
    "producer_text_changed_payload_conflicts": (
        "tests/test_t12_producer.py::test_text_conflicting_slot_fails_closed",
    ),
    "producer_duplicate_identical_judge_fails": (
        "tests/test_t12_producer.py::test_duplicate_text_slot_fails_even_when_identical",
    ),
    "producer_arbitrary_extra_field_rejected": (
        "tests/test_t12_assessment_planning.py::test_payload_and_provenance_closure_rejects_mutations",
    ),
    "producer_partial_d35_rejected": (
        "tests/test_t12_assessment_planning.py::test_payload_and_provenance_closure_rejects_mutations",
    ),
    "producer_judge_requires_compatible_exposure": (
        "tests/test_t12_producer.py::test_plan_from_unrelated_durable_history_is_rejected",
    ),
    "producer_speak_only_resumes_judge_only": (
        "tests/test_t12_producer.py::test_speech_exact_speak_missing_judge_resumes",
    ),
    "producer_judge_only_speech_fails": (
        "tests/test_t12_producer.py::test_speech_judge_without_speak_is_never_repaired",
    ),
    "producer_duplicate_identical_speak_fails": (
        "tests/test_t12_producer.py::test_duplicate_speech_slot_fails_closed",
    ),
    "producer_changed_retranscription_conflicts": (
        "tests/test_t12_producer.py::test_conflicting_speak_slot_fails_closed",
    ),
    "producer_changed_rejudgment_conflicts": (
        "tests/test_t12_producer.py::test_conflicting_speech_judge_fails_closed_without_append",
    ),
    "producer_speech_pair_exact_rerun_appends_zero": (
        "tests/test_t12_producer.py::test_speech_missing_pair_then_exact_rerun",
    ),

    # D58 identity and novelty obligations.
    "identity_l_voice_independent": (
        "tests/test_t12_foundation.py::test_l_voice_and_rendered_artifact_do_not_change_cognitive_identity",
    ),
    "identity_d54_trivial_formatting_independent": (
        "tests/test_t12_foundation.py::test_d54_formatting_normalization_preserves_identity",
    ),
    "identity_genuine_cognitive_change_distinct": (
        "tests/test_t12_foundation.py::test_real_cognitive_case_or_punctuation_difference_changes_identity",
    ),
    "identity_same_audio_changed_transcript_stable": (
        "tests/test_t12_speech_planning.py::test_same_raw_audio_under_changed_transcripts_keeps_attempt_and_assessment_identity",
    ),
    "identity_same_attempt_rejudged_stable": (
        "tests/test_t12_speech_planning.py::test_same_attempt_rejudged_keeps_assessment_id",
    ),
    "identity_attempt_stable_within_session": (
        "tests/test_t12_foundation.py::test_attempt_identity_is_stable_within_session_and_changes_for_new_session",
    ),
    "novelty_prior_omitted_consumed": (
        "tests/test_t12_foundation.py::test_any_prior_different_reserved_attempt_consumes_novelty",
    ),
    "novelty_prior_abstain_consumed": (
        "tests/test_t12_foundation.py::test_any_prior_different_reserved_attempt_consumes_novelty",
    ),
    "novelty_interrupted_reservation_consumed": (
        "tests/test_t12_foundation.py::test_any_prior_different_reserved_attempt_consumes_novelty",
    ),

    # D58 exposure crash obligations.
    "exposure_crash_before_reservation_no_display": (
        "tests/test_t12_foundation.py::test_failed_reservation_never_issues_permit",
    ),
    "exposure_crash_after_reservation_before_display_durable": (
        "tests/test_t12_foundation.py::test_post_append_novelty_failure_issues_no_permit_but_keeps_reservation",
    ),
    "exposure_crash_after_display_before_outcome_durable": (
        "tests/test_t12_foundation.py::test_reservation_is_durable_before_permit_and_permit_is_one_use",
    ),
    "exposure_restart_reservation_no_redisplay": (
        "tests/test_t12_foundation.py::test_restart_cannot_recreate_display_permit_from_reservation",
    ),
    "exposure_restart_capture_resumable_no_redisplay": (
        "tests/test_t12_foundation.py::test_valid_capture_is_resumable_without_creating_redisplay_authority",
    ),
    "capture_orphan_artifact_is_inert": (
        "tests/test_t12_foundation.py::test_crash_after_artifact_before_receipt_leaves_inert_orphan",
    ),

    # D58 speech-evidence obligations.
    "speech_raw_stt_cannot_establish_omission": (
        "tests/test_t12_speech_planning.py::test_unverified_target_absence_cannot_become_omitted",
    ),
    "speech_interruption_without_receipt_has_no_evidence": (
        "tests/test_t12_speech_planning.py::test_interruption_without_terminal_receipt_has_no_evidence",
    ),
    "speech_unverified_stt_cannot_enter_d19": (
        "tests/test_t12_speech_planning.py::test_unverified_stt_presence_cannot_reach_d19",
    ),
    "speech_confidence_threshold_cannot_establish_omission": (
        "tests/test_t12_speech_planning.py::test_stt_confidence_field_is_rejected_and_cannot_establish_omission",
    ),
    "speech_uncertain_is_abstain": (
        "tests/test_t12_speech_planning.py::test_uncertain_plans_exact_abstain_pair",
    ),
    "speech_failed_is_abstain": (
        "tests/test_t12_speech_planning.py::test_failed_plans_exact_abstain_pair",
    ),
    "speech_human_approved_success_may_enter_d19": (
        "tests/test_t12_speech_planning.py::test_only_success_transcription_can_enter_d19",
    ),
    "speech_human_confirmed_absence_is_omitted": (
        "tests/test_t12_speech_planning.py::test_success_target_absent_plans_exact_omitted_pair",
    ),
    "speech_passed_tracks_semantic_pass": (
        "tests/test_t12_speech_planning.py::test_speak_passed_tracks_semantic_outcome_not_transcription_success",
    ),
    "speech_speak_carries_zero_d35": (
        "tests/test_t12_speech_planning.py::test_speak_never_contains_d35",
    ),

    # D58 mandatory semantic anchors.
    "semantic_anchor_R_correct_paraphrase_pass": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_R_wrong_meaning_fail": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_R_off_topic_abstain": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_R_no_target_presence_gate": (
        "tests/test_t11_invariant_probes.py::test_r_and_l_do_not_gain_a_target_presence_gate",
    ),
    "semantic_anchor_L_correct_interpretation_pass": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_L_wrong_interpretation_fail": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_L_no_target_presence_gate": (
        "tests/test_t11_invariant_probes.py::test_r_and_l_do_not_gain_a_target_presence_gate",
    ),
    "semantic_anchor_W_correct_use_pass": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_W_target_absent_omitted": (
        "tests/test_t11_invariant_probes.py::test_productive_target_absence_is_omitted_and_never_fail",
    ),
    "semantic_anchor_W_semantic_misuse_fail": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_W_collocation_misuse_fail": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_S_verified_correct_use_pass": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_S_verified_misuse_fail": (
        "tests/test_t11_invariant_probes.py::test_d58_semantic_anchors_survive_exact_approve_path",
    ),
    "semantic_anchor_S_unverified_omission_abstain": (
        "tests/test_t12_speech_planning.py::test_unverified_target_absence_cannot_become_omitted",
    ),
    "semantic_anchor_S_transcription_uncertainty_abstain": (
        "tests/test_t11_invariant_probes.py::test_speech_transcription_uncertainty_is_abstain_not_learner_failure",
    ),
    "semantic_anchor_S_verified_absence_omitted": (
        "tests/test_t12_speech_planning.py::test_success_target_absent_plans_exact_omitted_pair",
    ),
    "semantic_anchor_reviewer_rejection_abstain": (
        "tests/test_t11_invariant_probes.py::test_reviewer_rejection_cannot_leave_pass_or_fail_as_accepted_evidence",
    ),

    # D68/D69 static authority, strict read, and lifecycle consumer closure.
    "static_eventlog_authority_real_tree": (
        "tests/test_t12_invariant_probes.py::test_d69_static_invariants_accept_real_tree",
    ),
    "static_eventlog_authority_mutations": (
        "tests/test_t12_producer.py::test_ast_invariant_rejects_authority_mutations",
    ),
    "strict_read_equivalence": (
        "tests/test_t12_invariant_probes.py::test_strict_reader_equivalence_table",
    ),
    "strict_read_recovery_torn_tail": (
        "tests/test_t12_invariant_probes.py::test_torn_tail_blocks_recovery_read",
    ),
    "strict_read_lifecycle_torn_tail": (
        "tests/test_t12_invariant_probes.py::test_torn_tail_blocks_lifecycle_read",
    ),
    "strict_read_r1_r2_authority": (
        "tests/test_t12_invariant_probes.py::test_lifecycle_read_matrix_rejects_mutations",
    ),
    "lifecycle_consumer_gate": (
        "tests/test_t12_invariant_probes.py::test_t12_lifecycle_consumer_gate",
    ),
    "lifecycle_legacy_compatibility": (
        "tests/test_t12_invariant_probes.py::test_legacy_generic_judge_remains_compatible",
    ),
    "lifecycle_anti_downgrade": (
        "tests/test_t12_invariant_probes.py::test_t12_lifecycle_consumer_gate",
    ),
    "frozen_envelope_v1_decodes_and_bears_lifecycle": (
        "tests/test_t12_invariant_probes.py::test_frozen_t12_v1_survives_future_generic_schema_registration",
    ),
    "frozen_envelope_requires_v1_decoder": (
        "tests/test_t12_invariant_probes.py::test_frozen_t12_v1_probe_requires_v1_decoder",
    ),
    "state_materializer_surface": (
        "tests/test_t12_invariant_probes.py::test_state_materializer_matrix_rejects_mutations",
    ),
    "state_materializer_indirect_authority": (
        "tests/test_t12_invariant_probes.py::test_state_materializer_indirect_authority_rejects_mutations",
    ),
    "concrete_events_import_allowlist": (
        "tests/test_t12_invariant_probes.py::test_concrete_events_import_allowlist_rejects_all_forms",
    ),
    "production_eventlog_construction_p1a": (
        "tests/test_t12_invariant_probes.py::test_p1a_rejects_production_eventlog_construction",
    ),

    # D68 producer crash probes retained as explicit gate obligations.
    "producer_crash_speak_append_failure": (
        "tests/test_t12_producer.py::test_speak_append_failure_never_attempts_judge",
    ),
    "producer_crash_durable_speak_return_failure": (
        "tests/test_t12_producer.py::test_speak_may_be_durable_when_append_raises_but_judge_is_not_attempted",
    ),
    "producer_crash_judge_append_resume": (
        "tests/test_t12_producer.py::test_judge_append_failure_after_speak_resumes_with_judge_only",
    ),
    "producer_crash_failed_confirmation": (
        "tests/test_t12_producer.py::test_failed_post_speak_confirmation_never_appends_judge",
    ),

    # D69 acceptance-gate obligations.
    "pytest_inventory_closed_unskippable": (
        "tests/test_t12_invariant_probes.py::test_registered_probe_inventory_is_closed_and_unskippable",
    ),
    "pytest_inventory_rejects_registry_mutations": (
        "tests/test_t12_invariant_probes.py::test_registered_probe_inventory_rejects_registry_mutations",
    ),
    "pytest_semantic_anchor_matrix_closed": (
        "tests/test_t12_invariant_probes.py::test_semantic_anchor_matrix_rejects_source_mutation",
    ),
    "pytest_unskippability_rejects_mutations": (
        "tests/test_t12_invariant_probes.py::test_registered_probe_unskippability_rejects_mutations",
    ),
    "pytest_required_phases_must_pass": (
        "tests/test_t12_invariant_probes.py::test_acceptance_gate_rejects_nonpassing_required_phases",
    ),
    "pytest_direct_filters_cannot_certify": (
        "tests/test_t12_invariant_probes.py::test_filtered_pytest_arguments_cannot_certify_acceptance",
    ),
    "pytest_hidden_filters_cannot_certify": (
        "tests/test_t12_invariant_probes.py::test_hidden_pytest_filtering_cannot_certify_acceptance",
    ),
    "pytest_discovery_config_cannot_certify": (
        "tests/test_t12_invariant_probes.py::test_discovery_configuration_cannot_certify_acceptance",
    ),
    "pytest_param_deselection_cannot_certify": (
        "tests/test_t12_invariant_probes.py::test_registered_param_deselection_cannot_certify_acceptance",
    ),
    "pytest_full_regression_failure_cannot_certify": (
        "tests/test_t12_invariant_probes.py::test_unrelated_regression_failure_cannot_certify_acceptance",
    ),

    # D69 real smoke scenarios.
    "real_smoke_s1_text_pass": (
        "tests/test_t12_real_smoke.py::test_s1_text_pass_is_lifecycle_bearing",
    ),
    "real_smoke_s2_dispositions": (
        "tests/test_t12_real_smoke.py::test_s2_policy_abstain_is_lifecycle_inert",
    ),
    "real_smoke_s3_speech_pair": (
        "tests/test_t12_real_smoke.py::test_s3_speech_pair_has_one_lifecycle_bearing_judge",
    ),
    "real_smoke_s4_exact_reruns": (
        "tests/test_t12_real_smoke.py::test_s4_exact_reruns_append_no_bytes_and_preserve_decision",
    ),
}

_COMMON_REQUIRED_CLAUSES = frozenset(
    clause for clause in D58_CLAUSES if not clause.startswith("semantic_anchor_")
)

REQUIRED_CLAUSES_BY_CHANNEL = {
    channel: _COMMON_REQUIRED_CLAUSES
    | {
        clause
        for clause in D58_CLAUSES
        if clause.startswith(f"semantic_anchor_{channel}_")
        or clause == "semantic_anchor_reviewer_rejection_abstain"
    }
    for channel in T12_LIFECYCLE_ENABLED_CHANNELS
}

REQUIRED_D58_SELECTORS = frozenset(
    selector
    for selectors in D58_PROBE_INVENTORY.values()
    for selector in selectors
)
