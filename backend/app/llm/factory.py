"""Provider chain from settings: LLM_PROVIDERS=ollama,groq,gemini. Unconfigured providers are skipped."""
from app.config import Settings

from .base import LLMError
from .gemini import GeminiLLM
from .groq_chat import GroqLLM
from .ollama import OllamaLLM


def build_llm_chain(s: Settings) -> list:
    chain = []
    for name in s.llm_provider_list:
        try:
            if name == "ollama":
                chain.append(OllamaLLM(s.ollama_base_url, s.ollama_model, s.ollama_num_gpu))
            elif name == "groq":
                chain.append(GroqLLM(s.groq_api_key, s.groq_llm_model))
            elif name == "gemini":
                chain.append(GeminiLLM(s.gemini_api_key, s.gemini_model))
        except LLMError:
            continue
    return chain
