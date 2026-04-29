"""
OpenCat - MCP Tools
=====================
Tool definitions for the OpenCat MCP server.
Each tool wraps a call to the backend HTTP API.

The MCP server does NOT access config.json or pet_state.json directly.
All data flows through the backend API at http://<backend_host>:<backend_port>.
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger("opencat.mcp.tools")

# Default backend URL (can be overridden via environment variable)
DEFAULT_BACKEND_URL = "http://127.0.0.1:9999"


class OpenCatClient:
    """HTTP client wrapper for calling the OpenCat backend API."""

    def __init__(self, backend_url: str = DEFAULT_BACKEND_URL):
        self.backend_url = backend_url.rstrip("/")

    async def _get(self, path: str) -> dict:
        """Make a GET request to the backend."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.backend_url}{path}", timeout=10.0)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, json_data: Optional[dict] = None) -> dict:
        """Make a POST request to the backend."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.backend_url}{path}",
                json=json_data or {},
                timeout=10.0,
            )
            if resp.status_code >= 400:
                detail = resp.json().get("detail", resp.text)
                return {"success": False, "error": detail}
            return resp.json()

    # ─── Tool Implementations ────────────────────────────────────────────

    async def list_adoptable_species(self) -> str:
        """Get the list of cat breeds available for adoption."""
        data = await self._get("/adoptable_species")
        species = data.get("species", [])
        lines = ["Available breeds for adoption:"]
        lines.append("")
        for s in species:
            lines.append(f"  • {s['id']} — {s['name']} ({s['name_zh']})")
        lines.append("")
        lines.append(f"Total: {len(species)} breeds")
        return "\n".join(lines)

    async def list_my_pets(self) -> str:
        """Get all currently adopted pets."""
        data = await self._get("/pets")
        pets = data.get("pets", [])
        if not pets:
            return "You have no pets yet. Use 'adopt_pet' to adopt one!"
        lines = ["Your adopted pets:"]
        lines.append("")
        for p in pets:
            lines.append(f"  🐱 {p['name']} ({p['species']}, {p['gender']})")
            lines.append(f"     ID: {p['id']}")
        lines.append("")
        lines.append(f"Total: {len(pets)} pet(s)")
        return "\n".join(lines)

    async def adopt_pet(self, name: str, species: str, gender: str) -> str:
        """Adopt a new pet."""
        result = await self._post("/pets/adopt", {
            "name": name,
            "species": species,
            "gender": gender,
        })
        if not result.get("success"):
            return f"❌ Adoption failed: {result.get('error', 'Unknown error')}"
        pet = result.get("pet", {})
        return (
            f"🎉 Successfully adopted {pet.get('name', name)}!\n"
            f"   Breed: {pet.get('species', species)}\n"
            f"   Gender: {pet.get('gender', gender)}\n"
            f"   Pet ID: {result.get('pet_id', 'unknown')}\n"
            f"   Remember to feed, water, and care for your new friend!"
        )

    async def get_pet_status(self, pet_id: str) -> str:
        """Get the current status and values of a specific pet."""
        try:
            # Signal 'agent=true' to trigger the Observer Effect (Pet reacts to being looked at)
            data = await self._get(f"/pets/{pet_id}/state?agent=true")
        except httpx.HTTPStatusError as e:
            return f"❌ Could not find pet with ID '{pet_id}'. Error: {e.response.status_code}"

        state_id = data.get("state_id", "unknown")
        values = data.get("values", {})
        is_sleeping = data.get("is_sleeping", False)
        action = data.get("action_active")
        traits_desc = data.get("traits_desc", [])

        # State emoji mapping
        state_emoji = {
            "idle": "😺", "happy": "😸", "eating": "🍽️", "drinking": "💧",
            "sleeping": "😴", "drowsy": "🥱", "hungry": "😿", "wanting": "🥺",
            "unhappy": "😾", "petted": "🥰", "sick": "🤒", "treating": "💊",
            "dead": "💔",
        }
        emoji = state_emoji.get(state_id, "🐱")

        lines = [f"{emoji} Pet Status: {state_id.upper()}"]

        # Dead pet: short-circuit with memorial message
        if state_id == "dead":
            lines.append("   Your pet has passed away.")
            lines.append("   You may adopt a new pet when you are ready.")
            return "\n".join(lines)

        if action:
            lines.append(f"   Currently: {action}")
        if is_sleeping:
            lines.append("   💤 Sleeping...")
        lines.append("")
        if traits_desc:
            lines.append(f"   ✨ Traits:      {', '.join(traits_desc)}")
        lines.append("   Values:")
        lines.append(f"     📅 Age:        {data.get('age_days', 0)} days")
        lines.append(f"     🍖 Hunger:     {values.get('hunger', 0):.0f}/100")
        lines.append(f"     💧 Thirst:     {values.get('thirst', 0):.0f}/100")
        lines.append(f"     😊 Happiness:  {values.get('happiness', 0):.0f}/100")
        lines.append(f"     🧹 Cleanliness:{values.get('cleanliness', 0):.0f}/100")
        lines.append(f"     ❤️ Health:     {values.get('health', 0):.0f}/100")
        lines.append(f"     ⚡ Energy:     {values.get('energy', 0):.0f}/100")

        # Warnings for low values
        warnings = []
        if values.get("hunger", 100) < 30:
            warnings.append("⚠️ Your pet is getting hungry! Consider feeding.")
        if values.get("thirst", 100) < 30:
            warnings.append("⚠️ Your pet is thirsty! Give some water.")
        if values.get("health", 100) < 40:
            warnings.append("🚨 Your pet's health is low! Consider medicating.")
        if values.get("health", 100) <= 0:
            warnings.append("🚨🚨 CRITICAL: Health is ZERO! Pet will die if not treated!")
        if values.get("happiness", 100) < 30:
            warnings.append("⚠️ Your pet is unhappy! Try petting or playing.")
        if values.get("cleanliness", 100) < 30:
            warnings.append("⚠️ Your pet needs cleaning!")

        if warnings:
            lines.append("")
            lines.extend(warnings)
            
        if data.get("current_speech"):
            lines.append(f"\n   💬 Last said: \"{data['current_speech']}\"")
            
        if data.get("unread_notifications"):
            num_notifs = len(data["unread_notifications"])
            lines.append(f"\n   📬 You have {num_notifs} unread message(s) from your pet! Use 'check_notifications'.")

        return "\n".join(lines)

    async def interact_with_pet(self, pet_id: str, action: str) -> str:
        """
        Perform an action on a pet.
        Valid actions: feed, water, pet, clean, medicate
        """
        valid_actions = ["feed", "water", "pet", "clean", "medicate"]
        if action not in valid_actions:
            return f"❌ Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}"

        result = await self._post(f"/pets/{pet_id}/{action}")
        if not result.get("success"):
            return f"❌ Action failed: {result.get('error', 'Unknown error')}"

        state = result.get("state", {})
        state_id = state.get("state_id", "unknown")
        values = state.get("values", {})

        action_descriptions = {
            "feed": "🍽️ Fed your pet! Hunger restored.",
            "water": "💧 Gave your pet water! Thirst quenched.",
            "pet": "🥰 Showed your pet some love! Happiness increased.",
            "clean": "🧹 Cleaned up! Cleanliness improved.",
            "medicate": "💊 Administered medicine! Health recovering.",
        }

        lines = [action_descriptions.get(action, f"✅ Performed '{action}'")]
        lines.append(f"   Current state: {state_id}")
        lines.append(f"   🍖 Hunger: {values.get('hunger', 0):.0f}  "
                      f"💧 Thirst: {values.get('thirst', 0):.0f}  "
                      f"❤️ Health: {values.get('health', 0):.0f}")
        return "\n".join(lines)

    async def get_all_pets_status(self) -> str:
        """Get a summary of all pets and their current states."""
        # Signal 'agent=true' to trigger the Observer Effect for all pets
        data = await self._get("/pets/all_states?agent=true")
        pets = data.get("pets", [])
        if not pets:
            return "No pets adopted yet."

        # Also fetch basic info
        info_data = await self._get("/pets")
        info_map = {p["id"]: p for p in info_data.get("pets", [])}

        lines = ["🐱 All Pets Overview:"]
        lines.append("")
        for p in pets:
            pid = p.get("pet_id", "?")
            info = info_map.get(pid, {})
            name = info.get("name", "Unknown")
            state = p.get("state_id", "unknown")
            vals = p.get("values", {})
            traits = p.get("traits", [])

            state_emoji = {
                "idle": "😺", "happy": "😸", "eating": "🍽️", "drinking": "💧",
                "sleeping": "😴", "drowsy": "🥱", "hungry": "😿", "wanting": "🥺",
                "unhappy": "😾", "petted": "🥰", "sick": "🤒", "treating": "💊",
                "dead": "💔",
            }
            emoji = state_emoji.get(state, "🐱")

            trait_str = f" [{','.join(traits)}]" if traits else ""
            lines.append(f"  {emoji} {name} [{state}]{trait_str}")
            lines.append(f"     🍖{vals.get('hunger',0):.0f} 💧{vals.get('thirst',0):.0f} "
                          f"😊{vals.get('happiness',0):.0f} ❤️{vals.get('health',0):.0f} "
                          f"⚡{vals.get('energy',0):.0f}")

            # Flag critical issues
            if vals.get("health", 100) < 30:
                lines.append("     🚨 CRITICAL: Health is very low!")
            elif vals.get("hunger", 100) < 20:
                lines.append("     ⚠️ Hungry!")
        
        lines.append("")
        lines.append(f"Total: {len(pets)} pet(s)")
        return "\n".join(lines)

    async def chat_with_pet(self, pet_id: str, message: str) -> str:
        """Send a chat message to the pet."""
        result = await self._post(f"/pets/{pet_id}/chat", {"message": message})
        if not result.get("success"):
            return f"❌ Chat failed: {result.get('error', 'Unknown error')}"
        return f"🐱 {result.get('reply')}"

    async def check_notifications(self, pet_id: str) -> str:
        """Check for unread notifications from the pet."""
        result = await self._get(f"/pets/{pet_id}/notifications")
        notifs = result.get("notifications", [])
        if not notifs:
            return "No new notifications."
        
        lines = ["📬 Pet Notifications:"]
        for i, n in enumerate(notifs, 1):
            lines.append(f"  {i}. {n}")
        return "\n".join(lines)
