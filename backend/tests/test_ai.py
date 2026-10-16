"""AI 规则解析（第一期）：模型用替身，校验依据文件 → 草稿 → 现有确认流程。"""
import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TFOS_DB', str(tmp_path / 'test.db'))
    for k in ('TFOS_AI_BASE_URL', 'TFOS_AI_MODEL', 'TFOS_AI_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    from app.db import init_db
    init_db()
    from app.main import app
    return TestClient(app)


FIN, TEAM = {'X-User-Id': 'u_fin'}, {'X-User-Id': 'u_team'}

CONTRACT = """定向井技术服务合同
第一条 服务内容：乙方为甲方提供定向井技术服务。
第五条 计价标准：定向井服务按服务天数计价，单价 38,000 元/天，以签证单为准。
第六条 复杂情况处理费用据实结算，双方另行协商。
第七条 测井服务按 52,000 元/井次结算。
"""

FAKE = {
    'clauses': [
        {'clause_no': '第五条', 'page': '', 'text': '定向井服务按服务天数计价，单价 38,000 元/天',
         'rules': [{'name': '定向井服务日费', 'item_code': 'C2-3-04', 'fact_type': '外包服务', 'line': 'C2-3-04',
                    'pricing_method': '单价', 'unit_price': 38000, 'unit': '天', 'base_item_code': '',
                    'conditions': [{'dimension': '市场', 'operator': '等于', 'value': '西北'},
                                   {'dimension': '区块', 'operator': '等于', 'value': '不存在的区块'}],
                    'combine_mode': '互斥', 'granularity': '逐日', 'valid_from': '2026-11-01',
                    'evidence': '定向井服务按服务天数计价，单价 38,000 元/天', 'confidence': 0.93,
                    'field_confidence': {'科目': 0.95, '触发事实': 0.9, '计价方式': 0.95, '单价': 0.97, '适用条件': 0.9,
                                         '组合方式': 0.9, '计算粒度': 0.9}}]},
        {'clause_no': '第七条', 'page': '',
         'rules': [{'name': '测井', 'item_code': 'C9-99', 'fact_type': '外包服务', 'pricing_method': '单价', 'unit_price': 52000,
                    'unit': '井次', 'evidence': '测井服务按 52,000 元/井次结算', 'confidence': 0.9},
                   {'name': '编造的条款', 'item_code': 'C2-3-14', 'fact_type': '外包服务', 'pricing_method': '单价', 'unit_price': 1,
                    'unit': '井次', 'evidence': '合同里根本没有这一句', 'confidence': 0.9}]},
    ],
    'manual': [{'clause_no': '第六条', 'text': '复杂情况处理费用据实结算，双方另行协商。', 'reason': '据实结算，写不成公式'}],
}


def upload(client, name='定向井合同.txt', data=CONTRACT.encode(), doc_type='外包服务合同', h=FIN):
    return client.post('/api/ai/docs', files={'file': (name, data)}, data={'doc_type': doc_type, 'doc_no': 'DX-2026-07',
                                                                          'counterparty': '某定向服务公司'}, headers=h)


def test_not_configured_records_failed_task(client):
    assert client.get('/api/ai/status', headers=FIN).json()['enabled'] is False
    doc = upload(client).json()
    r = client.post(f"/api/ai/docs/{doc['doc_id']}/parse", headers=FIN)
    assert r.status_code == 502 and '未配置' in r.json()['detail']
    tasks = client.get('/api/ai/tasks', headers=FIN).json()
    assert tasks[0]['status'] == '失败' and tasks[0]['doc_id'] == doc['doc_id']     # 失败也留痕
    assert client.get('/api/ai/status', headers=TEAM).status_code == 403
    assert upload(client, h=TEAM).status_code == 403


def test_upload_rejects_duplicates_and_unsupported(client):
    assert upload(client).status_code == 200
    assert upload(client).status_code == 409                                          # 同一文件指纹
    assert upload(client, name='x.doc', data=b'abc' * 20).status_code == 415
    assert upload(client, data=CONTRACT.encode(), doc_type='').status_code == 422


def test_docx_text_includes_tables():
    import docx
    from app import aiparse
    d = docx.Document()
    d.add_paragraph('补充协议 第二条 价格调整')
    t = d.add_table(rows=2, cols=3)
    for i, v in enumerate(['项目', '单位', '单价']):
        t.rows[0].cells[i].text = v
    for i, v in enumerate(['定向井服务', '天', '39000']):
        t.rows[1].cells[i].text = v
    buf = BytesIO()
    d.save(buf)
    text, _ = aiparse.extract_text('补充协议.docx', buf.getvalue())
    assert '价格调整' in text and '定向井服务 | 天 | 39000' in text


def test_parse_creates_checked_drafts(client, monkeypatch):
    from app import ai
    seen = {}

    def fake(messages):
        seen['messages'] = messages
        return '```json\n' + json.dumps(FAKE, ensure_ascii=False) + '\n```'
    monkeypatch.setattr(ai, 'call_model', fake)
    monkeypatch.setenv('TFOS_AI_BASE_URL', 'http://fake/v1')
    monkeypatch.setenv('TFOS_AI_MODEL', 'fake-model')
    doc = upload(client).json()
    out = client.post(f"/api/ai/docs/{doc['doc_id']}/parse", headers=FIN).json()
    # 系统字典随提示词下发，AI 只能用系统里的编码
    user_msg = seen['messages'][1]['content']
    assert 'C2-3-04' in user_msg and '定向井服务按服务天数计价' in user_msg
    assert len(out['created']) == 1 and len(out['skipped']) == 2 and len(out['manual']) == 1
    reasons = ' '.join(s['reason'] for s in out['skipped'])
    assert '科目 C9-99' in reasons and '找不到' in reasons                            # 科目不对、编造的原文都不采纳
    d = out['created'][0]
    assert '适用条件' in d['low_fields']                                              # 区块取值不在基础信息里：标低
    rule = client.get(f"/api/rules/{d['rule_id']}", headers=FIN).json()
    assert rule['rule']['status'] == '待确认' and rule['rule']['origin'] == 'AI提取' and rule['rule']['unit_price'] == 38000
    assert rule['rule']['ai_confidence'] < 0.6 and rule['clause']['hit'] == '定向井服务按服务天数计价，单价 38,000 元/天'
    assert rule['clause']['no'] == '第五条' and rule['rule']['valid_from'] == '2026-11-01'
    assert {c['dimension'] for c in rule['conditions']} == {'市场', '区块'}
    task = client.get('/api/ai/tasks', headers=FIN).json()[0]
    assert task['status'] == '完成' and task['model'] == 'fake-model' and task['draft_count'] == 1
    assert task['adoption'] == {'pending': 1, 'published': 0, 'modified': 0, 'rejected': 0}
    # 草稿走现有流程：确认 → 回算 → 待发布；AI 不能越过人工确认
    c = client.post(f"/api/rules/{d['rule_id']}/confirm", headers=FIN).json()
    assert c['rule']['status'] in ('待发布', '待确认')
    docs = client.get('/api/ai/docs', headers=FIN).json()
    assert docs[0]['parse_status'] == '已解析' and docs[0]['last_task']['draft_count'] == 1


def test_bad_model_output_fails_gracefully(client, monkeypatch):
    from app import ai
    monkeypatch.setattr(ai, 'call_model', lambda m: '抱歉，我无法处理')
    monkeypatch.setenv('TFOS_AI_BASE_URL', 'http://fake/v1')
    monkeypatch.setenv('TFOS_AI_MODEL', 'fake-model')
    doc = upload(client).json()
    r = client.post(f"/api/ai/docs/{doc['doc_id']}/parse", headers=FIN)
    assert r.status_code == 502 and 'JSON' in r.json()['detail']
    assert client.get('/api/ai/docs', headers=FIN).json()[0]['parse_status'] == '解析失败'


def test_evidence_matching_tolerates_pdf_line_breaks():
    from app import aiparse
    text = '第五条 定向井服务按服务\n天数计价，单价 38,000\n元/天。'
    assert aiparse.find_span(text, '定向井服务按服务天数计价，单价 38,000 元/天') is not None
    assert aiparse.find_span(text, '单价 40,000 元/天') is None
