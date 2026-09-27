"""LLM 模型工厂：默认走 DeepSeek V4 的 Anthropic 兼容端点，可由环境变量覆盖。

支持的环境变量：
  ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN   API Key（必填）
  ANTHROPIC_BASE_URL                          默认 https://api.deepseek.com/anthropic
  ANTHROPIC_MODEL                             默认 deepseek-v4-pro[1m]
"""
from __future__ import annotations
import os
from langchain_anthropic import ChatAnthropic


def get_model() -> ChatAnthropic:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key:
        raise RuntimeError(
            "缺少 LLM 密钥：请设置 ANTHROPIC_API_KEY（或 ANTHROPIC_AUTH_TOKEN）。"
        )
    return ChatAnthropic(
        model=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]"),
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
        api_key=key,
        max_tokens=4096,
        timeout=180,
    )
