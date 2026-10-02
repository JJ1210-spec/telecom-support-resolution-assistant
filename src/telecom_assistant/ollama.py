"""Small HTTP adapter for local Ollama embedding and JSON chat endpoints."""

from __future__ import annotations

import httpx


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, embed_model: str, chat_model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.embed_model = embed_model
        self.chat_model = chat_model

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed", json={"model": self.embed_model, "input": texts}
                )
                response.raise_for_status()
                vectors = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise OllamaError(f"Embedding model unavailable: {exc}") from exc
        if len(vectors) != len(texts) or not all(vectors):
            raise OllamaError("Ollama returned an unexpected embedding count or empty vector")
        return vectors

    async def chat_json(self, system: str, user: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.chat_model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        "stream": False,
                        "format": "json",
                        "think": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                import json

                result = json.loads(response.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise OllamaError(f"Chat model unavailable or invalid JSON: {exc}") from exc
        if not isinstance(result, dict):
            raise OllamaError("Chat model returned JSON that is not an object")
        return result
