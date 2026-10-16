"""AI 服务网关：统一走 OpenAI 兼容的 /chat/completions 接口，换模型只改配置，不改代码。

配置（~/tfos-data/env 或环境变量）：
  TFOS_AI_BASE_URL   接口地址，如私有部署的 vLLM http://gpu-server:8000/v1，或外部服务商的 OpenAI 兼容地址
  TFOS_AI_API_KEY    访问密钥（私有部署可不填）
  TFOS_AI_MODEL      模型名
  TFOS_AI_TIMEOUT    单次调用超时秒数，默认 180
不配置 TFOS_AI_BASE_URL 和 TFOS_AI_MODEL 即关闭 AI，规则仍可手工录入，现有流程不受影响。

四条原则（《主逻辑线》）：AI 不算钱、必须带出处、人工确认才生效、合同数据优先私有部署。
网关只负责“发提示词、收文本”，不接触数据库；结果由 aiparse 校验后才写成规则草稿。
"""
import json
import os
import re
import urllib.error
import urllib.request

PROMPT_VERSION = 'rule-parse-v1'


class AIError(Exception):
    pass


def config() -> dict:
    return {'base_url': os.environ.get('TFOS_AI_BASE_URL', '').rstrip('/'), 'api_key': os.environ.get('TFOS_AI_API_KEY', ''),
            'model': os.environ.get('TFOS_AI_MODEL', ''), 'timeout': float(os.environ.get('TFOS_AI_TIMEOUT', '180'))}


def enabled() -> bool:
    c = config()
    return bool(c['base_url'] and c['model'])


def status() -> dict:
    c = config()
    return {'enabled': enabled(), 'model': c['model'] or None, 'prompt_version': PROMPT_VERSION,
            'endpoint': re.sub(r'^(https?://[^/]+).*$', r'\1', c['base_url']) if c['base_url'] else None}


def call_model(messages: list[dict]) -> str:
    """发一次对话请求，返回模型输出的文本。测试中替换本函数即可模拟模型。"""
    c = config()
    if not enabled():
        raise AIError('未配置 AI 服务：请在 ~/tfos-data/env 设置 TFOS_AI_BASE_URL 和 TFOS_AI_MODEL')
    body = json.dumps({'model': c['model'], 'messages': messages, 'temperature': 0,
                       'response_format': {'type': 'json_object'}}, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if c['api_key']:
        headers['Authorization'] = f"Bearer {c['api_key']}"
    req = urllib.request.Request(c['base_url'] + '/chat/completions', data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=c['timeout']) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise AIError(f'AI 服务返回 {e.code}：{detail}') from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f'连不上 AI 服务：{e}') from e
    try:
        return data['choices'][0]['message']['content'] or ''
    except (KeyError, IndexError, TypeError) as e:
        raise AIError('AI 服务返回格式不正确') from e


def parse_json(text: str) -> dict:
    """模型输出转 JSON：容忍 ```json 代码块和前后多余文字。"""
    s = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', s, re.S)
    if m:
        s = m.group(1)
    a, b = s.find('{'), s.rfind('}')
    if a < 0 or b < a:
        raise AIError('AI 输出不是 JSON')
    try:
        return json.loads(s[a:b + 1])
    except json.JSONDecodeError as e:
        raise AIError(f'AI 输出的 JSON 无法解析：{e}') from e
