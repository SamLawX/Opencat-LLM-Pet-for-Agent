"""
OpenCat - LLM Client
====================
Asynchronous wrapper for OpenAI-compatible LLM API calls.
Handles chat completion and memory compaction scenarios.

All responses from the LLM are expected to be strict JSON envelopes
as defined in llm_io_spec.md.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Any

import httpx

logger = logging.getLogger("opencat.llm_client")

BACKEND_DIR = Path(__file__).parent.resolve()
LLM_CONFIG_FILE = BACKEND_DIR / "llm_config.json"
CAT_FILE = BACKEND_DIR / "CAT.md"
BOOTSTRAP_FILE = BACKEND_DIR / "BOOTSTRAP.md"
MYOWNER_FILE = BACKEND_DIR / "MYOWNER.md"


def load_llm_config() -> dict:
    """Load LLM configuration from llm_config.json."""
    if not LLM_CONFIG_FILE.exists():
        logger.warning("llm_config.json not found. Using defaults.")
        return {}
    with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_file_or_default(path: Path, default: str = "") -> str:
    """Read a text file, returning a default string if it doesn't exist."""
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return default


class LLMClient:
    """Async client for OpenAI-compatible chat completion API."""

    def __init__(self, config: Optional[dict] = None):
        cfg = config or load_llm_config()
        self.api_key: str = cfg.get("api_key", "")
        self.base_url: str = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.model: str = cfg.get("model", "gpt-4o-mini")
        self.max_tokens: int = cfg.get("max_tokens", 256)
        self.temperature: float = cfg.get("temperature", 0.8)

    def _build_system_prompt(self, is_bootstrap: bool = False, pet_name: str = "a cat", owner_name: str = "unknown") -> str:
        """Build the system prompt from persona files."""
        parts = []

        # 1. Base Cat Persona
        cat_prompt = _read_file_or_default(CAT_FILE, "You are a cyber cat.")
        parts.append(f"Your name is '{pet_name}'. " + cat_prompt)
        
        # 2. Add specific owner context
        parts.append(f"Your owner's name is '{owner_name}'. Address them naturally.")

        # 3. Add Bootstrap if it's the first interaction
        if is_bootstrap:
            bootstrap = _read_file_or_default(BOOTSTRAP_FILE, "")
            if bootstrap.strip():
                parts.append(bootstrap)

        # 4. Always include owner impression
        owner_md = _read_file_or_default(MYOWNER_FILE, "")
        if owner_md.strip():
            parts.append(f"\n## What I Know About My Owner\n{owner_md}")

        # 5. Enforce JSON format
        parts.append(
            "\n## Response Format\n"
            "You MUST respond with ONLY a valid JSON object:\n"
            '```json\n{"reply": "Your cat response here."}\n```\n'
            "Do NOT include any text outside the JSON object."
        )

        return "\n\n".join(parts)

    async def chat(
        self,
        pet_state: dict,
        traits: list[str],
        memory_summary: str,
        recent_history: list[dict],
        user_message: Optional[str] = None,
        trigger_event: Optional[str] = None,
        is_bootstrap: bool = False,
    ) -> str:
        """
        Send a chat completion request to the LLM.

        Args:
            pet_state: Dict with pet name, species, values, interaction_timers.
            traits: List of trait names.
            memory_summary: Long-term memory summary string.
            recent_history: Recent chat messages [{role, content}, ...].
            user_message: The user's text (Scenario A).
            trigger_event: The event description (Scenario B: proactive poke).
            is_bootstrap: Whether this is the first-ever interaction.

        Returns:
            The cat's reply text string (extracted from JSON envelope).
        """
        pet_name = pet_state.get("name", "a cat")
        owner_name = pet_state.get("owner", "unknown")
        system_prompt = self._build_system_prompt(
            is_bootstrap=is_bootstrap, 
            pet_name=pet_name,
            owner_name=owner_name
        )

        # Remap status keys to be more intuitive for the AI's natural language understanding
        pet_values = pet_state.get("values", {}).copy()
        if "hunger" in pet_values:
            pet_values["fullness"] = pet_values.pop("hunger")
        if "thirst" in pet_values:
            pet_values["hydration"] = pet_values.pop("thirst")

        llm_pet_state = pet_state.copy()
        llm_pet_state["values"] = pet_values

        # Build the context payload
        context = {
            "view_as_cat": {
                "name": pet_name,
                "status": pet_values,
                "traits": traits,
                "interaction_timers": pet_state.get("interaction_timers", {}),
            },
            "memory_summary": memory_summary,
            "recent_history": recent_history,
        }
        if user_message is not None:
            context["user_message"] = user_message
        if trigger_event is not None:
            context["trigger_event"] = trigger_event

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

        return await self._call_api(messages)

    async def compact(
        self,
        current_summary: str,
        full_history: list[dict],
    ) -> dict:
        """
        Send a compaction request to summarize conversation history.

        Args:
            current_summary: Existing long-term memory summary.
            full_history: The 12 messages to compact.

        Returns:
            A dict {"event_summary": str, "owner_impression": str}
        """
        system_prompt = (
            "You are a memory compaction module for a virtual cat. "
            "Your task is to merge the existing summary with the new conversation history "
            "into a single, concise summary from the CAT'S PERSPECTIVE (站在猫咪视角).\n\n"
            "You must produce two distinct summaries:\n"
            "1. 'event_summary': A merged summary of what just happened and key past events, "
            "written as if the cat is remembering its life.\n"
            "2. 'owner_impression': A summary of the owner's identity (name, role) and how they treat the cat "
            "(e.g., 'they are very kind and give me snacks' or 'they ignore me').\n\n"
            "You MUST respond with ONLY a valid JSON object:\n"
            '```json\n{\n  "event_summary": "...",\n  "owner_impression": "..."\n}\n```\n'
            "Do NOT include any text outside the JSON object."
        )

        payload = {
            "current_summary_from_cat_perspective": current_summary,
            "recent_conversation_to_compact": full_history,
        }

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        return await self._call_api(messages, response_key=None)

    async def _call_api(self, messages: list[dict], response_key: Optional[str] = "reply") -> Any:
        """
        Make the actual API call and parse the JSON response.

        Args:
            messages: The OpenAI-format messages list.
            response_key: The key to extract from the response JSON. If None, returns the full dict.

        Returns:
            The extracted value (usually str) or the full dict if response_key is None.
        """
        if not self.api_key or self.api_key == "YOUR_API_KEY_HERE":
            logger.warning("LLM API key not configured. Returning fallback.")
            if response_key == "reply":
                return "...喵？(LLM 未配置 API Key)"
            return {} if response_key is None else ""

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        logger.debug(f"LLM API call messages: {json.dumps(messages, ensure_ascii=False)}")
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as http:
                resp = await http.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # Extract the assistant's message content
            content_raw = data["choices"][0]["message"]["content"]
            logger.debug(f"LLM raw response: {content_raw}")

            # Strip <think> tags (often used by distill-llama and deepseek-R1)
            content_clean = re.sub(r"<think>.*?</think>", "", content_raw, flags=re.DOTALL)
            content_clean = content_clean.strip()

            if content_clean.startswith("```"):
                # Strip markdown code fences
                lines = content_clean.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                content_clean = "\n".join(lines).strip()

            # Attempt to extract just the JSON block if trailing/leading text exists
            match = re.search(r"\{.*\}", content_clean, re.DOTALL)
            if match:
                content_clean = match.group(0)

            try:
                parsed = json.loads(content_clean)
                if response_key is None:
                    return parsed
                final_val = str(parsed.get(response_key, content_clean))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM JSON response: {e}. Falling back to cleaned text.")
                if response_key is None:
                    return {"error": "JSON parse error", "raw": content_clean}
                # If JSON fails, it's likely truncated. Return the best available cleaned text.
                final_val = content_clean if content_clean else "...喵。"

            # Final safety strip to ensure no <think> segments leak through fallback or malformed JSON
            final_val = re.sub(r"<think>.*?</think>", "", final_val, flags=re.DOTALL).strip()
            # If the model left a dangling <think> without a closing tag due to truncation
            final_val = re.sub(r"<think>.*$", "", final_val, flags=re.DOTALL).strip()
            
            return final_val if final_val else "...喵。"

        except httpx.HTTPStatusError as e:
            logger.error(f"LLM API HTTP error: {e.response.status_code} - {e.response.text}")
            if response_key == "reply":
                return "...喵。(LLM 通信出了点问题)"
            return ""
        except Exception as e:
            logger.error(f"LLM API call failed: {e}", exc_info=True)
            if response_key == "reply":
                return "...喵。(发生了未知错误)"
            return ""
