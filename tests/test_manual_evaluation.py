"""Deliberate omissions/repeats/timing/role defects test the public metrics."""
from copy import deepcopy

import pytest

from karaoke_generator.manual_evaluation import evaluate_manual


def _project(words, *, canonical=None, review=True):
    annotations = [{"annotation_id": f"a{i}", "occurrence_id": f"o{i}", "unit": "word",
                    "text": word, "start": start, "end": end, "role_id": role}
                   for i, (word, start, end, role) in enumerate(words)]
    data = {"annotations": annotations,
            "occurrences": [{"occurrence_id": a["occurrence_id"], "annotation_ids": [a["annotation_id"]],
                             "complete": True} for a in annotations],
            "canonical_text": canonical if canonical is not None else " ".join(a["text"] for a in annotations),
            "source": {"audio_sha256": "audio", "lyrics_sha256": "lyrics"}, "reviews": []}
    if review:
        _review(data, 0, 20)
    return data


def _review(project, start, end, *, excluded=(), uncertainties=()):
    features = {}
    for a in project["annotations"]:
        if a["start"] < end and a["end"] > start:
            onset = start <= a["start"] < end
            mask = {"presence": onset, "text": onset, "start": onset,
                    "end": start < a["end"] <= end, "role": onset}
            features[a["annotation_id"]] = {f: known and f not in excluded for f, known in mask.items()}
    project["reviews"] = [{"review_id": "r1", "start": start, "end": end,
                           "all_roles": True, "features": features, "uncertainties": list(uncertainties)}]


def _parts(project, index, parts):
    original = project["annotations"].pop(index)
    added = [{**original, "annotation_id": f"{original['annotation_id']}-p{i}", "unit": "part",
              "text": text, "start": start, "end": end, "part_index": i}
             for i, (text, start, end) in enumerate(parts)]
    project["annotations"].extend(added)
    for occurrence in project["occurrences"]:
        if occurrence["occurrence_id"] == original["occurrence_id"]:
            occurrence["annotation_ids"] = [a["annotation_id"] for a in added]
    _review(project, 0, 20)
    return added


def test_missing_extra_echo_and_time_offset_are_measured_separately():
    truth = _project([("go", 1, 2, "A"), ("home", 3, 4, "A"), ("now", 6, 7, "B")])
    baseline = evaluate_manual(truth, deepcopy(truth))
    assert baseline["metrics"]["matched"] == 3
    assert baseline["metrics"]["missing"] == baseline["metrics"]["extra"] == 0
    assert baseline["metrics"]["start_median_ms"] == 0
    missing = _project([("go", 1, 2, "A"), ("now", 6, 7, "B")], review=False)
    omitted = evaluate_manual(truth, missing)
    assert omitted["metrics"]["missing"] == 1
    assert omitted["missing"] == ["o1"]
    extra = _project([("go", 1, 2, "A"), ("home", 3, 4, "A"), ("now", 6, 7, "B"),
                      ("home", 3, 4, "A")], review=False)
    repeated = evaluate_manual(truth, extra)
    assert repeated["metrics"]["matched"] == 3
    assert repeated["metrics"]["extra"] == 1
    shifted = deepcopy(truth)
    for annotation in shifted["annotations"]:
        annotation["start"] += .375
        annotation["end"] += .375
    delayed = evaluate_manual(truth, shifted)
    assert delayed["metrics"]["start_median_ms"] == delayed["metrics"]["end_median_ms"] == 375
    assert all(row["duration_error_ms"] == 0 for row in delayed["words"])


def test_global_role_permutation_is_free_but_role_switch_is_an_error():
    truth = _project([("one", 1, 2, "A"), ("two", 3, 4, "B"),
                      ("three", 5, 6, "A"), ("four", 7, 8, "B")])
    prediction = deepcopy(truth)
    for annotation in prediction["annotations"]:
        annotation["role_id"] = {"A": "Y", "B": "X"}[annotation["role_id"]]
    permuted = evaluate_manual(truth, prediction)
    assert permuted["metrics"]["role_errors"] == 0
    assert permuted["matching"]["predicted_to_reference_roles"] == {"Y": "A", "X": "B"}
    prediction["annotations"][2]["role_id"] = "X"
    switched = evaluate_manual(truth, prediction)
    assert switched["metrics"]["role_errors"] == 1
    assert switched["matching"]["role_mapping_scope"] == "whole-song"


def test_three_parts_are_one_word_but_part_role_switch_is_visible():
    truth = _project([("abracadabra", 1, 4, "A"), ("other", 7, 8, "B")])
    _parts(truth, 0, [("abra", 1, 2), ("ca", 2, 3), ("dabra", 3, 4)])
    prediction = deepcopy(truth)
    measured = evaluate_manual(truth, prediction)
    assert measured["metrics"]["reference_words"] == measured["metrics"]["matched"] == 2
    assert measured["coverage"]["role_observations"] == 4
    assert measured["metrics"]["role_errors"] == 0
    prediction["annotations"][2]["role_id"] = "B"
    assert evaluate_manual(truth, prediction)["metrics"]["role_errors"] == 1


def test_incomplete_group_stays_out_of_whole_word_metrics():
    truth = _project([("abracadabra", 1, 4, "A")])
    _parts(truth, 0, [("abra", 1, 2), ("ca", 2, 3), ("dabra", 3, 4)])
    truth["annotations"].pop()
    truth["occurrences"][0]["complete"] = False
    _review(truth, 0, 20)
    result = evaluate_manual(truth, deepcopy(truth))
    assert result["metrics"]["reference_words"] == result["metrics"]["matched"] == 0
    assert result["coverage"]["incomplete_groups"] == 1
    assert result["coverage"]["partial_annotations"] == result["coverage"]["matched_partial_annotations"] == 2


def test_unreviewed_boundaries_and_roles_never_receive_zero_error():
    truth = _project([("known", 1, 3, "A"), ("unknown", 8, 9, "B")], review=False)
    _review(truth, 0, 2, excluded=("start", "role"))
    prediction = deepcopy(truth)
    prediction["annotations"][0]["start"] = 1.5
    prediction["annotations"][0]["end"] = 3.4
    prediction["annotations"][0]["role_id"] = "wrong"
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["reference_words"] == 1
    assert result["metrics"]["start_median_ms"] is None
    assert result["metrics"]["end_median_ms"] is None
    assert result["metrics"]["role_errors"] is None
    assert result["coverage"]["starts"] == result["coverage"]["ends"] == 0
    assert result["words"][0]["duration_error_ms"] is None


def test_partial_boundary_prediction_is_reported_without_extra_penalty():
    truth = _project([], canonical="hello", review=False)
    _review(truth, 2, 4)
    prediction = _project([("hello", 1.5, 2.5, None), ("hello", 7, 8, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["extra"] == 0
    assert result["coverage"]["boundary_predictions"] == ["o0"]
    assert result["coverage"]["unreviewed_predictions"] == ["o1"]


def test_verified_empty_interval_can_find_extra_word():
    truth = _project([], canonical="hello", review=False)
    _review(truth, 2, 4)
    prediction = _project([("hello", 2.5, 3, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["reference_words"] == 0
    assert result["metrics"]["extra"] == 1


@pytest.mark.parametrize("features", [[], ["presence"]])
def test_uncertain_empty_interval_does_not_fabricate_extra_metric(features):
    truth = _project([], canonical="hello", review=False)
    _review(truth, 2, 4, uncertainties=[{"features": features, "start": 2, "end": 4, "reason": "uncertain"}])
    prediction = _project([("hello", 2.5, 3, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["extra"] == 0
    assert result["coverage"]["boundary_predictions"] == ["o0"]


def test_missing_reference_and_unreviewed_project_are_unavailable():
    project = _project([("home", 1, 2, None)], review=False)
    for reference in [None, project]:
        result = evaluate_manual(reference, project)
        assert result["status"] == "unavailable"
        assert result["metrics"] is None


def test_later_uncertainty_masks_older_review_only_in_its_interval():
    truth = _project([], canonical="hello", review=False)
    _review(truth, 0, 10)
    truth["reviews"].append({"review_id": "r2", "start": 2, "end": 4, "all_roles": True,
                            "features": {}, "uncertainties": [{"features": [], "start": 2, "end": 4}]})
    prediction = _project([("hello", 2.5, 3, None), ("hello", 6, 7, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["extra"] == 1
    assert result["extra"] == ["o1"]
    assert result["coverage"]["boundary_predictions"] == ["o0"]


def test_human_words_outside_txt_are_explicit_not_algorithm_targets():
    truth = _project([("home", 1, 2, None), ("handwritten", 3, 4, None)], canonical="home")
    prediction = _project([("home", 1, 2, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["reference_words"] == 1
    assert result["metrics"]["missing"] == 0
    assert result["coverage"]["outside_source_text"] == ["o1"]


def test_foreign_source_cannot_receive_metrics():
    truth = _project([("home", 1, 2, None)])
    prediction = deepcopy(truth)
    prediction["source"]["audio_sha256"] = "foreign audio"
    with pytest.raises(ValueError, match="исходникам"):
        evaluate_manual(truth, prediction)


def test_matching_repeated_text_is_one_to_one_by_nearest_times():
    truth = _project([("home", 1, 2, None), ("home", 4, 5, None), ("home", 7, 8, None)])
    prediction = _project([("home", 7.1, 8.1, None), ("home", 1.1, 2.1, None)], review=False)
    result = evaluate_manual(truth, prediction)
    assert result["metrics"]["matched"] == 2
    assert result["metrics"]["missing"] == 1
    assert result["missing"] == ["o1"]
    assert {(r["reference"], r["prediction"]) for r in result["words"]} == {("o0", "o1"), ("o2", "o0")}
