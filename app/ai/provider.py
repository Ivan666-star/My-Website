"""统一的 AI Provider 抽象层。

规格书第三十节要求：不要把系统锁死在一个模型上。
这里统一走 OpenAI 兼容协议，DeepSeek / 智谱 GLM / 通义千问 / Kimi / OpenAI / 本地 Ollama
只需要改 .env 里的 AI_BASE_URL + AI_MODEL + AI_API_KEY 即可切换。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from ..config import settings


class AIError(RuntimeError):
    pass


class AIProvider:
    """所有 Provider 的基类。"""

    name = "base"

    def chat(
        self,
        system: str,
        user: str,
        *,
        strong: bool = False,
        temperature: float | None = None,
        max_tokens: int = 8000,
        retries: int = 2,
    ) -> str:
        raise NotImplementedError

    def chat_vision(
        self,
        system: str,
        user: str,
        image_data_url: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 4000,
        retries: int = 1,
    ) -> str:
        """带图提问。不是所有模型都能识图，基类默认给出可操作的提示。"""
        raise AIError(
            f"当前模型（{getattr(self, 'model', '未配置')}）不支持识图。"
            "数学题拍照录题需要一个支持视觉的模型。"
        )


class OpenAICompatProvider(AIProvider):
    """任何 OpenAI 兼容的 /chat/completions 服务。"""

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, model_strong: str | None = None,
                 model_vision: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_strong = model_strong or model
        # 识图单独一个型号（.env 里的 AI_MODEL_VISION）；留空就跟着 model 走
        self.model_vision = model_vision or model
        self._client = httpx.Client(timeout=settings.ai_timeout)

    def _chat_messages(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float | None,
        max_tokens: int,
        retries: int,
    ) -> str:
        """发一次 /chat/completions 并返回正文。chat() 与 chat_vision() 共用它。"""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": settings.ai_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    raise AIError(f"HTTP {resp.status_code}: {resp.text[:400]}")
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                content = (msg.get("content") or "").strip()
                if choice.get("finish_reason") == "length":
                    if attempt < retries:
                        max_tokens = min(max(int(max_tokens), 1) * 2, 32000)
                        payload["max_tokens"] = max_tokens
                        last_err = AIError("模型输出达到长度限制，正在增加额度重试")
                        time.sleep(1)
                        continue
                    raise AIError("模型输出达到长度限制，内容不完整，请稍后重试")
                # 推理模型（DeepSeek V4 系 flash / v4-pro）会先产出 reasoning_content，
                # 如果 max_tokens 太小，思考过程会把额度吃光 → content 为空串。
                # 这种情况下调用方会静默走兜底逻辑（分类退化成规则匹配），非常难查，
                # 所以这里主动放大额度重试一次。
                if not content and msg.get("reasoning_content"):
                    if attempt < retries:
                        max_tokens = min(max(int(max_tokens), 1) * 4, 32000)
                        payload["max_tokens"] = max_tokens
                        last_err = AIError(f"推理模型正文为空，放大 max_tokens 到 {max_tokens} 重试")
                        time.sleep(1)
                        continue
                    raise AIError("模型只返回了推理内容、没有正文（max_tokens 可能太小）")
                return content
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < retries:
                    time.sleep(2 + attempt * 3)
        raise AIError(f"AI 调用失败：{last_err}")

    def chat(
        self,
        system: str,
        user: str,
        *,
        strong: bool = False,
        temperature: float | None = None,
        max_tokens: int = 8000,
        retries: int = 2,
    ) -> str:
        return self._chat_messages(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=self.model_strong if strong else self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            retries=retries,
        )

    def chat_vision(
        self,
        system: str,
        user: str,
        image_data_url: str,
        *,
        temperature: float | None = None,
        max_tokens: int = 4000,
        retries: int = 1,
    ) -> str:
        """把图片按 OpenAI 兼容的多模态格式发过去（content 数组 + image_url）。

        ⚠️ 能识图的是 **deepseek-flash**；`deepseek-v4-pro` 与旧的 `deepseek-chat`
        都看不了图（实测会回「无法读取图片」），换模型时别把这条弄丢。
        """
        if not image_data_url:
            raise AIError("没有拿到图片内容")
        return self._chat_messages(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            model=self.model_vision,
            temperature=temperature,
            max_tokens=max_tokens,
            retries=retries,
        )



class NotConfiguredProvider(AIProvider):
    """未配置 API Key 时的占位实现：给出明确、可操作的中文提示，而不是静默失败。"""

    name = "not-configured"

    def chat(self, system: str, user: str, **kwargs) -> str:  # noqa: ARG002
        raise AIError(
            "尚未配置 AI 模型。请在项目根目录的 .env 文件里填写 AI_API_KEY、"
            "AI_BASE_URL、AI_MODEL，然后重启服务。"
        )

    def chat_vision(self, system: str, user: str, image_data_url: str, **kwargs) -> str:  # noqa: ARG002
        raise AIError(
            "尚未配置 AI 模型，没法识图。请在项目根目录的 .env 里填好 "
            "AI_API_KEY / AI_BASE_URL / AI_MODEL（能识图的是 deepseek-flash），然后重启服务。"
        )


_provider: AIProvider | None = None


def get_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.ai_ready:
            _provider = OpenAICompatProvider(
                settings.ai_base_url,
                settings.ai_api_key,
                settings.ai_model,
                settings.ai_model_strong,
                settings.ai_model_vision,
            )
        else:
            _provider = NotConfiguredProvider()
    return _provider


def reset_provider() -> None:
    """修改 .env 后无需重启即可生效。"""
    global _provider
    _provider = None


# ---------------- JSON 解析工具 ----------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str) -> Any:
    """从模型输出里稳健地抽出 JSON。"""
    if not text:
        raise AIError("模型返回为空")
    raw = text.strip()

    m = _FENCE.search(raw)
    if m:
        raw = m.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 退一步：截取第一个 { 到最后一个 }
    start_obj, end_obj = raw.find("{"), raw.rfind("}")
    start_arr, end_arr = raw.find("["), raw.rfind("]")
    candidates = []
    if start_obj != -1 and end_obj > start_obj:
        candidates.append(raw[start_obj : end_obj + 1])
    if start_arr != -1 and end_arr > start_arr:
        candidates.append(raw[start_arr : end_arr + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise AIError("模型返回的内容不是合法 JSON，无法解析")
