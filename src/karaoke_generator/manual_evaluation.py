"""Occurrence/role evaluation with explicit human coverage, independent of display notes."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from statistics import median

EVALUATOR_VERSION = 'manual-occurrences-1'
FEATURES = ('presence', 'text', 'start', 'end', 'role')
MATCH_WINDOW = 2.0  # Frozen for both sides of every comparison, in original seconds.


def normalize(text):
    return ''.join(re.findall(r'\w+', str(text).casefold(), re.UNICODE))


def assignment(costs):
    """Minimum-cost rectangular assignment (rows <= columns), Hungarian potentials."""
    if not costs:
        return []
    n, m = len(costs), len(costs[0])
    if n > m:
        raise ValueError('Assignment requires at least as many columns as rows')
    u, v, p, way = [0.]*(n+1), [0.]*(m+1), [0]*(m+1), [0]*(m+1)
    for i in range(1, n+1):
        p[0], j0 = i, 0
        minimum, used = [math.inf]*(m+1), [False]*(m+1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, m+1):
                if not used[j]:
                    current = costs[i0-1][j-1]-u[i0]-v[j]
                    if current < minimum[j]:
                        minimum[j], way[j] = current, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(m+1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return [(p[j]-1, j-1) for j in range(1, m+1) if p[j]]


def _timed(item):
    a, b = item.get('start'), item.get('end')
    return isinstance(a, (float, int)) and isinstance(b, (float, int)) and math.isfinite(a) and math.isfinite(b) and 0 <= a < b


def aggregate(project):
    groups = defaultdict(list)
    definitions = {o['occurrence_id']: o for o in project.get('occurrences', [])}
    for a in project.get('annotations', []):
        groups[a['occurrence_id']].append(a)
    result = []
    for oid, parts in groups.items():
        parts.sort(key=lambda a: (a.get('part_index', 0), a.get('start') or 0, a['annotation_id']))
        definition = definitions.get(oid, {})
        timed = all(_timed(a) for a in parts)
        expected = set(definition.get('annotation_ids', [a['annotation_id'] for a in parts]))
        result.append({'id': oid, 'parts': parts,
                       'text': parts[0]['text'] if len(parts) == 1 else ''.join(p['text'] for p in parts),
                       'complete': definition.get('complete', True) and expected == {a['annotation_id'] for a in parts},
                       'start': min(p['start'] for p in parts) if timed else None,
                       'end': max(p['end'] for p in parts) if timed else None})
    return result


def masks(project):
    combined = defaultdict(lambda: {f: False for f in FEATURES})
    for review in project.get('reviews', []):
        for aid, mask in review.get('features', {}).items():
            for feature in FEATURES:
                combined[aid][feature] |= mask.get(feature) is True
    return combined


def _match(truth, predicted):
    costs = []
    for t in truth:
        row = []
        for p in predicted:
            gap = max(t['start']-p['end'], p['start']-t['end'], 0) if _timed(t) and _timed(p) else math.inf
            valid = normalize(t['text']) == normalize(p['text']) and gap <= MATCH_WINDOW
            row.append(abs(t['start']-p['start'])+abs(t['end']-p['end']) if valid else 1e8)
        costs.append(row+[1e5]*len(truth))
    return [(i, j) for i, j in assignment(costs) if j < len(predicted) and costs[i][j] < 1e5]


def _coverage_for(group, coverage):
    parts = group['parts']
    starts = min(parts, key=lambda a: a.get('start') if _timed(a) else math.inf)
    ends = max(parts, key=lambda a: a.get('end') if _timed(a) else -math.inf)
    return {'presence': coverage[starts['annotation_id']]['presence'],
            'text': all(coverage[a['annotation_id']]['text'] for a in parts),
            'start': coverage[starts['annotation_id']]['start'],
            'end': coverage[ends['annotation_id']]['end'],
            'role': all(coverage[a['annotation_id']]['role'] for a in parts)}


def _full_interval(item, reviews):
    if not _timed(item):
        return False
    # Empty feature lists mean doubt about every feature, as in storage. A later
    # uncertainty also masks older overlapping reviews (including empty spans,
    # where no annotation-level mask can carry that doubt).
    for review in reviews:
        for uncertain in review.get('uncertainties', []):
            if not uncertain.get('features') or 'presence' in uncertain['features']:
                start = uncertain.get('start', review['start'])
                end = uncertain.get('end', review['end'])
                if item['start'] < end and item['end'] > start:
                    return False
    intervals = sorted((r['start'], r['end']) for r in reviews if r.get('all_roles') is True)
    cursor = item['start']
    for a, b in intervals:
        if a <= cursor < b:
            cursor = max(cursor, b)
    return cursor >= item['end']


def _touches(item, reviews):
    return _timed(item) and any(item['start'] < r['end'] and item['end'] > r['start'] for r in reviews)


def evaluate_manual(reference, prediction):
    """No reference means unavailable, never a zero-error result.

    Global role permutation is solved once for the song. Parts remain role observations,
    while complete occurrence groups are the only units in word metrics.
    """
    if reference is None:
        return {'status': 'unavailable', 'reason': 'Нет проверенного эталона', 'metrics': None,
                'evaluator_version': EVALUATOR_VERSION}
    truth_project = reference.get('project', reference.get('snapshot', reference))
    if 'annotations' not in truth_project:
        raise ValueError('Эталон не содержит снимок разметки')
    source = truth_project.get('source', {})
    other = prediction.get('source', {})
    for key in ('audio_sha256', 'lyrics_sha256'):
        if source.get(key) and other.get(key) and source[key] != other[key]:
            raise ValueError('Эталон и результат относятся к разным исходникам')
    coverage = masks(truth_project)
    all_truth, all_pred = aggregate(truth_project), aggregate(prediction)
    outside = [g['id'] for g in all_truth if normalize(g['text']) not in {normalize(w) for w in truth_project.get('canonical_text', '').split()}]
    eligible, incomplete = [], []
    for group in all_truth:
        group['coverage'] = _coverage_for(group, coverage)
        if group['complete']:
            if _timed(group) and group['coverage']['presence'] and group['coverage']['text'] and group['id'] not in outside:
                eligible.append(group)
        else:
            incomplete.append(group)
    predicted = [p for p in all_pred if p['complete'] and _timed(p)]
    paired = _match(eligible, predicted)
    used_t, used_p = {i for i, _ in paired}, {j for _, j in paired}
    role_observations, rows = [], []
    for i, j in paired:
        t, p = eligible[i], predicted[j]
        rows.append({'reference': t['id'], 'prediction': p['id'], 'text': t['text'],
                     'start_error_ms': round(abs(t['start']-p['start'])*1000, 3) if t['coverage']['start'] else None,
                     'end_error_ms': round(abs(t['end']-p['end'])*1000, 3) if t['coverage']['end'] else None,
                     'duration_error_ms': round(abs((t['end']-t['start'])-(p['end']-p['start']))*1000, 3)
                     if t['coverage']['start'] and t['coverage']['end'] else None})
        for part in t['parts']:
            if not coverage[part['annotation_id']]['role']:
                continue
            # Each truth part votes against all overlapping predicted parts. This exposes
            # a role switch inside a word rather than replacing it with majority identity.
            candidates = [a for a in p['parts'] if _timed(a) and a['start'] < part['end'] and a['end'] > part['start']]
            if not candidates:
                candidates = [min(p['parts'], key=lambda a: abs((a.get('start') or 0)-part['start']))]
            for candidate in candidates:
                role_observations.append((part.get('role_id'), candidate.get('role_id'), part['annotation_id']))
    true_roles = sorted({a for a, _, _ in role_observations if a is not None})
    pred_roles = sorted({b for _, b, _ in role_observations if b is not None})
    counts = [[sum(a == tr and b == pr for a, b, _ in role_observations) for pr in pred_roles] for tr in true_roles]
    role_map = {pred_roles[j]: true_roles[i] for i, j in assignment([[-n for n in row]+[0]*len(true_roles) for row in counts])
                if j < len(pred_roles) and counts[i][j]}
    role_errors = sum((b is not None if a is None else b is None or role_map.get(b) != a) for a, b, _ in role_observations)
    extras, boundary, unreviewed = [], [], []
    protected_truth = [g for g in all_truth if g['id'] not in {t['id'] for t in eligible}]
    for j, p in enumerate(predicted):
        if j in used_p:
            continue
        if any(_timed(t) and normalize(t['text']) == normalize(p['text']) and t['start'] < p['end'] and t['end'] > p['start'] for t in protected_truth):
            boundary.append(p['id'])
        elif _full_interval(p, truth_project.get('reviews', [])):
            extras.append(p['id'])
        elif _touches(p, truth_project.get('reviews', [])):
            boundary.append(p['id'])
        else:
            unreviewed.append(p['id'])
    start_errors = [r['start_error_ms'] for r in rows if r['start_error_ms'] is not None]
    end_errors = [r['end_error_ms'] for r in rows if r['end_error_ms'] is not None]
    part_truth = [dict(a, id=a['annotation_id']) for g in incomplete for a in g['parts']
                  if _timed(a) and coverage[a['annotation_id']]['presence'] and coverage[a['annotation_id']]['text']]
    part_pred = [dict(a, id=a['annotation_id']) for g in all_pred for a in g['parts'] if _timed(a)]
    part_pairs = _match(part_truth, part_pred)
    has_reviews = bool(truth_project.get('reviews'))
    return {'status': 'measured' if has_reviews else 'unavailable', 'evaluator_version': EVALUATOR_VERSION,
            'matching': {'method': 'one-to-one minimum time cost for same text', 'window_seconds': MATCH_WINDOW,
                         'role_mapping_scope': 'whole-song', 'predicted_to_reference_roles': role_map},
            'metrics': {'reference_words': len(eligible), 'matched': len(paired),
                        'missing': len(eligible)-len(used_t), 'extra': len(extras),
                        'start_median_ms': median(start_errors) if start_errors else None,
                        'end_median_ms': median(end_errors) if end_errors else None,
                        'role_errors': role_errors if role_observations else None} if has_reviews else None,
            'coverage': {'reviewed_intervals': truth_project.get('reviews', []), 'total_reference_occurrences': len(all_truth),
                         'scored_words': len(eligible), 'starts': len(start_errors), 'ends': len(end_errors),
                         'role_observations': len(role_observations), 'incomplete_groups': len(incomplete),
                         'partial_annotations': len(part_truth), 'matched_partial_annotations': len(part_pairs),
                         'boundary_predictions': boundary, 'unreviewed_predictions': unreviewed,
                         'outside_source_text': outside},
            'words': rows, 'missing': [g['id'] for i, g in enumerate(eligible) if i not in used_t], 'extra': extras}
