"""HTTPX Embeddings protocol and finite float32 vectors for pgvector."""

import asyncio
import math
import struct
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, StrictFloat, StrictInt, ValidationError

from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings
from learntrail_agent.observability import observed


def invalid_embedding() -> AppError:
    return AppError("EMBEDDING_INVALID_RESPONSE", "Embedding 服务返回了无效向量或响应。", 502)


def validate_vector(values: list[float], dimensions: int) -> list[float]:
    if not 1 <= dimensions <= 16000 or len(values) != dimensions:
        raise invalid_embedding()
    result = []
    for value in values:
        if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 3.4028234663852886e38:
            raise invalid_embedding()
        result.append(struct.unpack("!f", struct.pack("!f", value))[0])
    if not any(result):
        raise invalid_embedding()
    return result


class EmbeddingUsage(BaseModel):
    prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)


class EmbeddingItem(BaseModel):
    index: StrictInt = Field(ge=0)
    embedding: list[Annotated[StrictFloat, Field(allow_inf_nan=False)]] = Field(min_length=1, max_length=16000)


class EmbeddingResponse(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    data: list[EmbeddingItem] = Field(min_length=1, max_length=128)
    usage: EmbeddingUsage | None = None


class EmbeddingBatch(BaseModel):
    vectors: list[list[float]] = Field(repr=False)
    model: str
    usage: EmbeddingUsage | None


class EmbeddingClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http, self.settings = http, settings

    def check_configuration(self) -> None:
        if not self.settings.embedding_configured:
            raise AppError("EMBEDDING_NOT_CONFIGURED", "请配置独立的 Embedding 地址、密钥、模型和维度。", 503)

    @observed("embedding.batch","embedding")
    async def _batch(self, inputs: list[str]) -> EmbeddingBatch:
        payload: dict[str, object] = {"model": self.settings.embedding_model, "input": inputs}
        if self.settings.embedding_send_dimensions:
            payload["dimensions"] = self.settings.embedding_dimensions
        try:
            async with asyncio.timeout(self.settings.embedding_timeout_seconds):
                response = await self.http.post(
                    f"{self.settings.embedding_base_url}/embeddings", json=payload,
                    headers={"Authorization": f"Bearer {self.settings.embedding_api_key.get_secret_value()}"},
                    timeout=httpx.Timeout(self.settings.embedding_timeout_seconds,
                                          connect=min(self.settings.llm_connect_timeout_seconds,
                                                      self.settings.embedding_timeout_seconds)),
                )
                if response.status_code in {401, 403}:
                    raise AppError("EMBEDDING_AUTH_ERROR", "Embedding 服务认证失败。", 502)
                if response.status_code == 429:
                    raise AppError("EMBEDDING_RATE_LIMITED", "Embedding 服务限流。", 429, True)
                if response.status_code >= 500:
                    raise AppError("EMBEDDING_UNAVAILABLE", "Embedding 服务暂不可用。", 503, True)
                if not 200 <= response.status_code < 300:
                    raise AppError("EMBEDDING_REQUEST_REJECTED", "Embedding 服务拒绝了请求。", 502)
                parsed = EmbeddingResponse.model_validate(response.json())
                if len(parsed.data) != len(inputs) or sorted(item.index for item in parsed.data) != list(range(len(inputs))):
                    raise invalid_embedding()
                vectors = [validate_vector(item.embedding, self.settings.embedding_dimensions)
                           for item in sorted(parsed.data, key=lambda item: item.index)]
                return EmbeddingBatch(vectors=vectors, model=parsed.model, usage=parsed.usage)
        except (httpx.TimeoutException, TimeoutError):
            raise AppError("EMBEDDING_TIMEOUT", "Embedding 请求超时。", 504, True) from None
        except httpx.HTTPError:
            raise AppError("EMBEDDING_CONNECTION_ERROR", "无法连接 Embedding 服务。", 502, True) from None
        except (ValueError, ValidationError, OverflowError):
            raise invalid_embedding() from None

    async def embed(self, inputs: str | list[str]) -> EmbeddingBatch:
        self.check_configuration()
        texts = [inputs] if isinstance(inputs, str) else inputs
        if (not isinstance(texts, list) or not 1 <= len(texts) <= 10000
                or any(not isinstance(text, str) or not text.strip() or len(text) > 200000 for text in texts)):
            raise AppError("INVALID_EMBEDDING_INPUT", "Embedding 输入必须是有界的非空文本。", 422)
        batches: list[EmbeddingBatch] = []
        for offset in range(0, len(texts), self.settings.embedding_batch_size):
            batches.append(await self._batch(texts[offset:offset+self.settings.embedding_batch_size]))
        if len({batch.model for batch in batches}) != 1:
            raise invalid_embedding()
        usage = None
        if all(batch.usage is not None for batch in batches):
            values = {}
            for name in ("prompt_tokens", "total_tokens"):
                counts = [getattr(batch.usage, name) for batch in batches]
                values[name] = sum(counts) if all(value is not None for value in counts) else None
            usage = EmbeddingUsage(**values)
        return EmbeddingBatch(vectors=[vector for batch in batches for vector in batch.vectors],
                              model=batches[0].model, usage=usage)
