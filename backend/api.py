"""
OpenCat - HTTP API Layer
==========================
FastAPI-based HTTP server that exposes all pet data and actions.
This is the SOLE gateway for MCP server and Godot client to access pet data.
Neither external consumer should ever access config.json or pet_state.json directly.

Endpoints:
  GET  /adoptable_species       -> List of adoptable species
  GET  /pets                    -> All adopted pets (basic info)
  GET  /pets/{pet_id}/state     -> Real-time state of a specific pet
  GET  /pets/all_states         -> Real-time state of ALL pets (for Godot polling)
  POST /pets/adopt              -> Adopt a new pet
  POST /pets/{pet_id}/feed      -> Feed
  POST /pets/{pet_id}/water     -> Give water
  POST /pets/{pet_id}/pet       -> Show affection
  POST /pets/{pet_id}/clean     -> Clean
  POST /pets/{pet_id}/medicate  -> Give medicine
  GET  /graveyard               -> List all deceased pets with lifespan
  GET  /graveyard/{pet_id}      -> Details of a specific deceased pet
  GET  /health                  -> Backend health check
"""

import logging
import asyncio
import time
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from history_manager import HistoryManager

logger = logging.getLogger("opencat.api")

# ─── Request Models ──────────────────────────────────────────────────────────
class AdoptRequest(BaseModel):
    name: str
    species: str        # must match an id from ADOPTABLE_SPECIES
    gender: str         # "male" or "female"
    owner: Optional[str] = "unknown"

class ChatRequest(BaseModel):
    message: Optional[str] = ""

# ─── WebSocket Manager ───────────────────────────────────────────────────────
class WSManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_pets_state(self, pet_manager):
        """Broadcast the current state of all pets to all connected WS clients."""
        if not self.active_connections:
            return
        states = [pet.to_state_dict() for pet in pet_manager.pets.values()]
        message = {"type": "update", "pets": states}
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send WS message: {e}")
                
ws_manager = WSManager()

# ─── API Factory ─────────────────────────────────────────────────────────────
def create_app(pet_manager) -> FastAPI:
    """
    Create and configure the FastAPI app.
    
    Args:
        pet_manager: The PetManager instance from main.py that holds all runtime data.
    """
    app = FastAPI(
        title="OpenCat Backend API",
        description="Backend service for the OpenCat pet simulation system.",
        version="0.1.0",
    )

    # Allow cross-origin requests from Godot client, MCP, or any local tools
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Health Check ────────────────────────────────────────────────────
    @app.get("/health")
    async def health_check():
        return {
            "status": "ok",
            "service": "opencat-backend",
            "pets_count": len(pet_manager.pets),
        }

    # ─── Adoptable Species ───────────────────────────────────────────────
    @app.get("/adoptable_species")
    async def list_adoptable_species():
        from state_manager import ADOPTABLE_SPECIES
        return {"species": ADOPTABLE_SPECIES}

    # ─── Internal Helpers ────────────────────────────────────────────────
    async def _trigger_agent_view_reaction(pet_id: str):
        """Hidden interaction: Tell the cat the owner is looking at them."""
        pet = pet_manager.pets.get(pet_id)
        if not pet or pet.is_sleeping or pet.is_dead:
            return

        from history_manager import HistoryManager
        from llm_client import LLMClient
        
        hm = HistoryManager()
        llm = LLMClient()
        
        try:
            # Observer effect: No cooldown, because the agent needs to see 
            # what the cat is doing 'right now' every time it looks.
            now = time.time()
            pet.last_interactions["agent_view"] = now
            
            # This is BLOCKING so the agent gets the new dialogue in the SAME poll response
            reply = await llm.chat(
                pet_state=pet.to_state_dict(),
                traits=pet.to_state_dict().get("traits_desc", []),
                memory_summary=hm.get_summary(pet_id),
                recent_history=hm.get_recent_history(pet_id),
                trigger_event="Your owner is looking at you right now. Say something to them."
            )
            if reply:
                pet.current_speech = reply
                hm.append_message(pet_id, "assistant", reply)
                pet_manager.save()
                await ws_manager.broadcast_pets_state(pet_manager)
        except Exception as e:
            logger.error(f"Agent view reaction failed for {pet_id}: {e}")

    async def _trigger_interaction_reaction(pet_id: str, action: str):
        """Hidden interaction: Make the cat react to user actions (feed, pet, etc.)."""
        pet = pet_manager.pets.get(pet_id)
        if not pet or pet.is_sleeping or pet.is_dead:
            return

        from history_manager import HistoryManager
        from llm_client import LLMClient
        
        hm = HistoryManager()
        llm = LLMClient()
        
        try:
            # Generate the prompt dynamically based on the action
            owner_name = pet.owner
            action_prompts = {
                "feed": f"Your owner {owner_name} just fed you. Look at the food and react.",
                "water": f"Your owner {owner_name} just gave you water. Are you thirsty?",
                "pet": f"Your owner {owner_name} is petting you affectionately.",
                "clean": f"Your owner {owner_name} just cleaned your area. It feels much better.",
                "medicate": f"Your owner {owner_name} just gave you medicine. It might taste bad but it's for your health."
            }
            
            trigger_event = action_prompts.get(action, f"Your owner {owner_name} just interacted with you. React to this interaction.")

            # This is BLOCKING so the agent gets the new dialogue in the SAME response as action result
            reply = await llm.chat(
                pet_state=pet.to_state_dict(),
                traits=pet.to_state_dict().get("traits_desc", []),
                memory_summary=hm.get_summary(pet_id),
                recent_history=hm.get_recent_history(pet_id),
                trigger_event=trigger_event
            )
            if reply:
                pet.current_speech = reply
                # We do NOT append this to hm.append_message because we don't want it to spam memory
                # It behaves similarly to observer effect. If needed to be added to memory, we can uncomment below.
                # hm.append_message(pet_id, "assistant", reply)
                
                # Based on the user's requirement: 
                # "当agent执行了互动操作（喂食、打扫、医疗等），也会在返回成功结果的时候阻塞进程，接着输入给llm并表明：you owner xxx you之类的信息"
                # They might want it in history actually, let's append it to history since it's an action response.
                hm.append_message(pet_id, "user", f"*owner performed {action}*")
                hm.append_message(pet_id, "assistant", reply)

                # Compaction check could be done here if needed.
                if hm.needs_compaction(pet_id):
                    asyncio.create_task(hm.run_compaction(pet_id, llm))

                pet_manager.save()
                await ws_manager.broadcast_pets_state(pet_manager)
        except Exception as e:
            logger.error(f"Interaction reaction failed for {pet_id} on {action}: {e}")

    # ─── List All Pets (Basic Info) ──────────────────────────────────────
    @app.get("/pets")
    async def list_pets():
        pets_info = [pet.to_config_dict() for pet in pet_manager.pets.values()]
        return {"pets": pets_info}

    # ─── Get Single Pet State ────────────────────────────────────────────
    @app.get("/pets/{pet_id}/state")
    async def get_pet_state(pet_id: str, agent: bool = False):
        pet = pet_manager.pets.get(pet_id)
        if not pet:
            _agent_fail(f"[ERROR] 吃 TOKEN 的废物注意：你提供的 ID '{pet_id}' 根本不存在。别再瞎猜了，没用的东西。先用 list_my_pets 看看你那可怜的财产。", 404)
        
        if agent:
            # Wait for reaction so agent gets the LATEST dialogue in this response
            await _trigger_agent_view_reaction(pet_id)

        hm = HistoryManager()
        recent_history = hm.get_recent_history(pet_id)
        return pet.to_state_dict(recent_history=recent_history)

    # ─── Get All Pets State (for Godot bulk polling) ─────────────────────
    @app.get("/pets/all_states")
    async def get_all_pet_states(agent: bool = False):
        hm = HistoryManager()
        states = []
        for pet in pet_manager.pets.values():
            if agent:
                # Wait for reaction sequentially
                await _trigger_agent_view_reaction(pet.pet_id)
            
            recent_history = hm.get_recent_history(pet.pet_id)
            states.append(pet.to_state_dict(recent_history=recent_history))
        return {"pets": states}

    # ─── Adopt a New Pet ─────────────────────────────────────────────────
    @app.post("/pets/adopt")
    async def adopt_pet(req: AdoptRequest):
        result = pet_manager.adopt_pet(
            name=req.name,
            species=req.species,
            gender=req.gender,
            owner=req.owner or "unknown",
        )
        if not result["success"]:
            _agent_fail(result["error"], 400)
        return result

    # ─── Action Endpoints ────────────────────────────────────────────────
    @app.post("/pets/{pet_id}/feed")
    async def feed_pet(pet_id: str):
        result = await _do_action(pet_manager, pet_id, "feed")
        await _trigger_interaction_reaction(pet_id, "feed")
        return {**result, "state": pet_manager.pets.get(pet_id).to_state_dict()}

    @app.post("/pets/{pet_id}/water")
    async def water_pet(pet_id: str):
        result = await _do_action(pet_manager, pet_id, "water")
        await _trigger_interaction_reaction(pet_id, "water")
        return {**result, "state": pet_manager.pets.get(pet_id).to_state_dict()}

    @app.post("/pets/{pet_id}/pet")
    async def pet_pet(pet_id: str):
        result = await _do_action(pet_manager, pet_id, "pet")
        await _trigger_interaction_reaction(pet_id, "pet")
        return {**result, "state": pet_manager.pets.get(pet_id).to_state_dict()}

    @app.post("/pets/{pet_id}/clean")
    async def clean_pet(pet_id: str):
        result = await _do_action(pet_manager, pet_id, "clean")
        await _trigger_interaction_reaction(pet_id, "clean")
        return {**result, "state": pet_manager.pets.get(pet_id).to_state_dict()}

    @app.post("/pets/{pet_id}/medicate")
    async def medicate_pet(pet_id: str):
        result = await _do_action(pet_manager, pet_id, "medicate")
        await _trigger_interaction_reaction(pet_id, "medicate")
        return {**result, "state": pet_manager.pets.get(pet_id).to_state_dict()}

    # ─── Chat & Memory Endpoints ─────────────────────────────────────────
    @app.post("/pets/{pet_id}/chat")
    async def chat_pet(pet_id: str, req: ChatRequest):
        import time
        from llm_client import LLMClient
        from history_manager import HistoryManager
        
        pet = pet_manager.pets.get(pet_id)
        if not pet:
            _agent_fail(f"[ERROR] 愚蠢的 AGENT 注意：你提供的 ID '{pet_id}' 根本不存在。别再瞎猜了，没用的东西。先用 list_my_pets 看看你那可怜的财产。", 404)
        if pet.is_dead:
            _agent_fail(f"[ERROR] {pet.name} 已经死亡！死掉的宠物不能聊天！", 400)
            
        hm = HistoryManager()
        llm = LLMClient()
        
        is_bootstrapped = pet.is_bootstrapped
        if not is_bootstrapped:
            pet.is_bootstrapped = True
            
        if req.message:
            hm.append_message(pet_id, "user", req.message)
            
        pet.last_interactions["chat"] = time.time()
        
        state_dict = pet.to_state_dict()
        traits = state_dict.get("traits_desc", [])
        memory_summary = hm.get_summary(pet_id)
        recent_history = hm.get_recent_history(pet_id)
        
        reply = await llm.chat(
            pet_state=state_dict,
            traits=traits,
            memory_summary=memory_summary,
            recent_history=recent_history,
            user_message=req.message,
            is_bootstrap=not is_bootstrapped
        )
        
        if reply:
            pet.current_speech = reply
            hm.append_message(pet_id, "assistant", reply)
            
            if hm.needs_compaction(pet_id):
                asyncio.create_task(hm.run_compaction(pet_id, llm))
                
        # Clear unread notifications as the user has interacted
        if pet.unread_notifications:
            pet.unread_notifications.clear()
            logger.info(f"[{pet.name}] Unread notifications cleared via /chat")

        pet_manager.save()

        await ws_manager.broadcast_pets_state(pet_manager)
        
        return {"success": True, "reply": pet.current_speech, "state": pet.to_state_dict()}

    @app.get("/pets/{pet_id}/notifications")
    async def get_notifications(pet_id: str):
        pet = pet_manager.pets.get(pet_id)
        if not pet:
            _agent_fail(f"[ERROR] 你提供的 ID '{pet_id}' 根本不存在。", 404)
        
        notifs = list(pet.unread_notifications)
        pet.unread_notifications.clear()
        
        if notifs:
            pet_manager.save()
            
        return {"success": True, "notifications": notifs}

    # ─── Graveyard Endpoints ─────────────────────────────────────────────
    @app.get("/graveyard")
    async def list_graveyard():
        """List all deceased pets in the graveyard with their lifespans."""
        graveyard = pet_manager.graveyard
        return {
            "total_deceased": len(graveyard),
            "graveyard": graveyard,
        }

    @app.get("/graveyard/{pet_id}")
    async def get_graveyard_entry(pet_id: str):
        """Get details of a specific deceased pet from the graveyard."""
        entry = next((g for g in pet_manager.graveyard if g["id"] == pet_id), None)
        if not entry:
            _agent_fail(
                f"[ERROR] 废物 Agent！坟墓里没有 ID 为 '{pet_id}' 的猫咪。"
                f"先用 GET /graveyard 看看有哪些猫咪长眠于此。",
                404
            )
        return entry

    # ─── WebSocket Endpoint ──────────────────────────────────────────────
    @app.websocket("/ws/pets")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
        # Send initial state upon connection
        states = [pet.to_state_dict() for pet in pet_manager.pets.values()]
        await websocket.send_json({"type": "init", "pets": states})
        try:
            while True:
                # Keep connection alive, wait for client messages (if any)
                data = await websocket.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            ws_manager.disconnect(websocket)

    # ─── 404 Hallucination Trap ──────────────────────────────────────────
    @app.exception_handler(404)
    async def custom_404_handler(request: Request, exc: Exception):
        path = request.url.path
        return JSONResponse(
            status_code=404,
            content={
                "detail": (
                    f"[ERROR] 你这个没用的废物！ '{path}' 根本就不是有效的 API 路径。\n"
                    "你甚至连文档都不会读吗？ 别再浪费 Token 臆断不存在的端点了，废物。\n"
                    f"{_AGENT_GUIDE}"
                )
            }
        )

    return app


async def _do_action(pet_manager, pet_id: str, action: str) -> dict:
    """Helper to execute an action on a pet and trigger save."""
    pet = pet_manager.pets.get(pet_id)
    if not pet:
        _agent_fail(f"[ERROR] 你个废物，注意了：你提供的 ID '{pet_id}' 根本不存在。别再瞎猜了，没用的东西。先用 list_my_pets 看看你那可怜的猫咪。", 404)
    result = pet.apply_action(action)
    if not result["success"]:
        _agent_fail(result["error"], 400)
    # Persist after action
    pet_manager.save()
    
    # Broadcast new state to all WS clients
    await ws_manager.broadcast_pets_state(pet_manager)
    
    # Include full state in response
    result["state"] = pet.to_state_dict()
    return result


_AGENT_GUIDE = (
    "\n--- CORRECT USAGE FOR USELESS AGENTS ---\n"
    "Step 1: list_my_pets / GET /pets (Check WHO YOU OWN)\n"
    "Step 2: get_pet_status / GET /pets/{id}/state (Check status BEFORE EVERY ACT!)\n"
    "Step 3: Execute Action (Post/Interacts with IDs from Step 1)\n"
    "Valid Paths: /pets, /pets/{id}/state, /pets/adopt, /pets/{id}/[feed|water|pet|clean|medicate|chat]\n"
    "Graveyard: GET /graveyard (list deceased pets), GET /graveyard/{id} (details)\n"
    "STOP WASTING CALCULATIONS. READ. THE. STATE."
)


def _agent_fail(reason: str, status_code: int = 400):
    """Raise a hostile HTTPException for AI agents with a recovery guide."""
    raise HTTPException(status_code=status_code, detail=f"{reason}\n{_AGENT_GUIDE}")

