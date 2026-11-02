#!/usr/bin/env python3
"""Pure exact taxonomy matching and bounded suggestions; no network."""
import difflib
from normalize import normalize

PLURAL = {'project_category': 'project_categories', 'project': 'projects', 'type': 'types', 'model': 'models'}


def match(taxonomy, kind, intent):
    rows = taxonomy.get(PLURAL[kind], [])
    selectable = [row for row in rows if row.get('selectable_for_upload') is not False]
    result = dict(status='unresolved', kind=kind, input=intent, candidates=[], reason='缺少名称或 id')
    def hit(row, via):
        return dict(status='matched', kind=kind, selector={'id': row['id']}, term=row, via=via, candidates=[])
    if intent.get('id') is not None:
        for row in selectable:
            if row['id'] == intent['id']:
                return hit(row, 'id')
        result['reason'] = 'id 不存在或看得见但不能用于上传；不自动按名称新建'
        return result
    if intent.get('name') is None:
        return result
    try:
        display, key = normalize(intent['name'])
    except ValueError:
        result['reason'] = '词条名称非法'
        return result
    result.update(suggested_new_name=display, normalized=key, reason='未精确命中，需要选择或逐项确认新建')
    for row in selectable:
        if normalize(row['name'])[1] == key:
            return hit(row, 'name')
    for row in selectable:
        if key in row.get('aliases', []):
            return hit(row, 'alias')
    if any(normalize(row['name'])[1] == key or key in row.get('aliases', []) for row in rows if row.get('selectable_for_upload') is False):
        result['reason'] = '看得见但不能用于上传；只有明确确认才可新建自己的项目'
    if len(selectable) > 200:
        result['reason'] = '候选过多，请给出规范名或 id'
        return result
    for row in selectable:
        other = normalize(row['name'])[1]
        score = difflib.SequenceMatcher(None, key, other).ratio()
        if score >= .80 or (min(len(key), len(other)) >= 2 and (key.startswith(other) or other.startswith(key))):
            result['candidates'].append(dict(id=row['id'], name=row['name'], score=round(score, 2)))
    result['candidates'].sort(key=lambda x: (-x['score'], x['id']))
    result['candidates'] = result['candidates'][:5]
    return result
