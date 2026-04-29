"""
OpenCat - History Manager
=========================
Manages per-pet chat history in markdown format.
Handles reading, writing, counting messages, and triggering compaction.

History files are stored at: backend/history/{pet_id}.md
Each message is stored as a markdown section with role and content.

Compaction is triggered every 12 messages (6 rounds of user + assistant).
"""

import json
import re
import logging
import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opencat.history_manager")

BACKEND_DIR = Path(__file__).parent.resolve()
HISTORY_DIR = BACKEND_DIR / "history"
MYOWNER_FILE = BACKEND_DIR / "MYOWNER.md"

# Compaction threshold: after this many messages, compact
COMPACTION_THRESHOLD = 12


class HistoryManager:
    """Manages per-pet conversation history and memory compaction."""

    def __init__(self):
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    def _history_path(self, pet_id: str) -> Path:
        """Get the history file path for a specific pet."""
        return HISTORY_DIR / f"{pet_id}.md"

    def _summary_path(self, pet_id: str) -> Path:
        """Get the summary file path for a specific pet."""
        return HISTORY_DIR / f"{pet_id}_summary.txt"

    def read_history(self, pet_id: str) -> list[dict]:
        """
        Read chat history from the markdown file.

        Returns a list of {role, content} dicts.
        """
        path = self._history_path(pet_id)
        if not path.exists():
            return []

        content = path.read_text(encoding="utf-8", errors="replace")
        messages = []

        # Parse markdown sections: ### user [timestamp] / ### assistant [timestamp]
        # The timestamp in brackets is optional for backward compatibility.
        pattern = r"### (user|assistant)(?: \[(.*?)\])?\n(.*?)(?=\n### |\Z)"
        for match in re.finditer(pattern, content, re.DOTALL):
            role = match.group(1)
            timestamp = match.group(2) or ""
            text = match.group(3).strip()
            if text:
                messages.append({
                    "role": role, 
                    "content": text,
                    "timestamp": timestamp
                })

        return messages

    def append_message(self, pet_id: str, role: str, content: str) -> int:
        """
        Append a message to the history file.

        Args:
            pet_id: The pet's unique ID.
            role: "user" or "assistant".
            content: The message text.

        Returns:
            The total number of messages after appending.
        """
        path = self._history_path(pet_id)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n### {role} [{timestamp}]\n{content}\n")

        messages = self.read_history(pet_id)
        count = len(messages)
        logger.debug(f"[{pet_id}] Appended {role} message. Total: {count}")
        return count

    def get_message_count(self, pet_id: str) -> int:
        """Get the current number of messages in history."""
        return len(self.read_history(pet_id))

    def needs_compaction(self, pet_id: str) -> bool:
        """Check if the history has reached the compaction threshold."""
        return self.get_message_count(pet_id) >= COMPACTION_THRESHOLD

    def get_summary(self, pet_id: str) -> str:
        """Read the existing long-term memory summary for a pet."""
        path = self._summary_path(pet_id)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace").strip()
        return ""

    def save_summary(self, pet_id: str, summary: str) -> None:
        """Save the compacted summary for a pet."""
        path = self._summary_path(pet_id)
        path.write_text(summary, encoding="utf-8")
        logger.info(f"[{pet_id}] Summary updated ({len(summary)} chars)")

    def clear_history(self, pet_id: str) -> None:
        """Clear the history file after compaction."""
        path = self._history_path(pet_id)
        if path.exists():
            path.write_text("", encoding="utf-8", errors="replace")
            logger.info(f"[{pet_id}] History cleared after compaction")

    def update_owner_impression(self, new_summary: str) -> None:
        """
        Update MYOWNER.md with content derived from the latest compaction.
        The summary serves as the cat's evolving impression of its owner.
        """
        try:
            MYOWNER_FILE.write_text(
                f"# My Owner\n\n{new_summary}\n",
                encoding="utf-8",
            )
            logger.info(f"MYOWNER.md updated ({len(new_summary)} chars)")
        except IOError as e:
            logger.error(f"Failed to update MYOWNER.md: {e}")

    def reset_owner_info(self) -> None:
        """
        Reset MYOWNER.md to a blank state.
        Called when a new pet is adopted after a previous one died,
        so the new cat starts with a fresh impression of the owner.
        """
        try:
            MYOWNER_FILE.write_text(
                "# My Owner\n\n*A new journey begins...*\n",
                encoding="utf-8",
            )
            logger.info("MYOWNER.md reset for new adoption.")
        except IOError as e:
            logger.error(f"Failed to reset MYOWNER.md: {e}")

    async def run_compaction(self, pet_id: str, llm_client) -> Optional[str]:
        """
        Execute compaction: summarize the current 12 messages and update memory.

        Args:
            pet_id: The pet's ID.
            llm_client: An LLMClient instance for the compaction call.

        Returns:
            The new summary text, or None on failure.
        """
        messages = self.read_history(pet_id)
        if len(messages) < COMPACTION_THRESHOLD:
            logger.debug(f"[{pet_id}] Not enough messages for compaction ({len(messages)}/{COMPACTION_THRESHOLD})")
            return None

        current_summary = self.get_summary(pet_id)

        # Take exactly the first COMPACTION_THRESHOLD messages for compaction
        to_compact = messages[:COMPACTION_THRESHOLD]

        logger.info(f"[{pet_id}] Running compaction on {len(to_compact)} messages...")

        try:
            compaction_data = await llm_client.compact(
                current_summary=current_summary,
                full_history=to_compact,
            )

            if compaction_data and isinstance(compaction_data, dict):
                event_summary = compaction_data.get("event_summary")
                owner_impression = compaction_data.get("owner_impression")

                if event_summary:
                    self.save_summary(pet_id, event_summary)
                
                if owner_impression:
                    self.update_owner_impression(owner_impression)

                if event_summary or owner_impression:
                    self.clear_history(pet_id)

                    # If there were messages beyond the compaction threshold, re-add them
                    remaining = messages[COMPACTION_THRESHOLD:]
                    for msg in remaining:
                        self.append_message(pet_id, msg["role"], msg["content"])

                    logger.info(f"[{pet_id}] Compaction complete. Event summary length: {len(event_summary or '')}")
                    return event_summary or owner_impression
            else:
                logger.warning(f"[{pet_id}] Compaction returned invalid data: {compaction_data}")
                return None

        except Exception as e:
            logger.error(f"[{pet_id}] Compaction failed: {e}", exc_info=True)
            return None

    def get_recent_history(self, pet_id: str, max_messages: int = 10) -> list[dict]:
        """Get the most recent messages from history (for LLM context)."""
        messages = self.read_history(pet_id)
        return messages[-max_messages:] if len(messages) > max_messages else messages
