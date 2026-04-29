"""
OpenCat - Backend Main Entry Point
=====================================
Runs both the HTTP API server and the background state evolution loop.

Architecture:
  - FastAPI (uvicorn) serves HTTP on a configurable port (default: 8000)
  - A background asyncio task periodically decays pet values
  - Persistence is saved to config.json and pet_state.json on every change
  - Graceful shutdown on SIGTERM/SIGINT saves state before exit

Usage:
  python main.py                     # Run with defaults (port 8000)
  python main.py --port 9000         # Custom port
  python main.py --host 0.0.0.0      # Listen on all interfaces (headless server)
  python main.py --tick-interval 30  # Custom tick interval in seconds
"""

import os
import sys
import json
import time
import signal
import asyncio
import logging
import argparse
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn

# Ensure the backend directory is in sys.path for imports
BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

from state_manager import PetData, ADOPTABLE_SPECIES
from api import create_app, ws_manager

# ─── Configuration ───────────────────────────────────────────────────────────
CONFIG_FILE = BACKEND_DIR / "config.json"
STATE_FILE  = BACKEND_DIR / "pet_state.json"
GRAVEYARD_FILE = BACKEND_DIR / "graveyard.json"
LOG_FILE    = BACKEND_DIR / "service.log"
SECRET_FILE = BACKEND_DIR / ".secret"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999
DEFAULT_TICK_INTERVAL = 30  # seconds between state evolution ticks


# ─── Logging Setup ───────────────────────────────────────────────────────────
def setup_logging():
    """Configure logging to both console and file."""
    log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )

logger = logging.getLogger("opencat.main")


# ─── Pet Manager ─────────────────────────────────────────────────────────────
class PetManager:
    """
    Central manager that holds all pet data in memory.
    Handles persistence (load/save) and adoption logic.
    """

    def __init__(self):
        self.pets: dict[str, PetData] = {}  # pet_id -> PetData
        self.graveyard: list[dict] = []     # archived deceased pets
        self._dirty = False
        self.secret_key = self._load_or_create_secret()

    def _load_or_create_secret(self) -> str:
        if SECRET_FILE.exists():
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        else:
            secret = uuid.uuid4().hex + uuid.uuid4().hex
            with open(SECRET_FILE, "w", encoding="utf-8") as f:
                f.write(secret)
            return secret

    def load(self) -> None:
        """Load pets from config.json and pet_state.json."""
        configs = []
        states = []

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    configs = data.get("pets", [])
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load config.json: {e}")

        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    states = data.get("pets", [])
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load pet_state.json: {e}")

        # Build a state lookup by pet_id
        state_map = {s["id"]: s for s in states if "id" in s}

        for cfg in configs:
            pet_id = cfg.get("id")
            if not pet_id:
                continue
            state = state_map.get(pet_id, {})
            pet = PetData.from_persistence(cfg, state)
            
            self.pets[pet_id] = pet
            logger.info(f"Loaded pet: {pet.name} ({pet.species}) [{pet_id}]")

            # Migration: Ensure traits and signature are present and saved
            needs_save = False
            if not pet.signature:
                logger.info(f"Migrating unsigned pet [{pet_id}] and generating signature.")
                pet.signature = pet.generate_signature(self.secret_key)
                needs_save = True
            
            # If the config didn't have traits, they were generated in __init__
            if "traits" not in cfg:
                logger.info(f"Migrating pet [{pet_id}] with new random traits: {pet.traits}")
                needs_save = True
            
            if needs_save:
                self._dirty = True

        logger.info(f"Loaded {len(self.pets)} pet(s) from persistence.")
        
        if self._dirty:
            self.save()
            self._dirty = False

        # Load graveyard
        self.load_graveyard()

    def load_graveyard(self) -> None:
        """Load the graveyard archive from graveyard.json."""
        if GRAVEYARD_FILE.exists():
            try:
                with open(GRAVEYARD_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.graveyard = data.get("graveyard", [])
                logger.info(f"Loaded {len(self.graveyard)} pet(s) from graveyard.")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load graveyard.json: {e}")
                self.graveyard = []
        else:
            self.graveyard = []

    def save_graveyard(self) -> None:
        """Save graveyard data to graveyard.json."""
        try:
            with open(GRAVEYARD_FILE, "w", encoding="utf-8") as f:
                json.dump({"graveyard": self.graveyard}, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save graveyard.json: {e}")

    def add_to_graveyard(self, pet: PetData) -> dict:
        """Archive a deceased pet to the graveyard. Returns the graveyard entry."""
        # Avoid duplicates
        if any(g["id"] == pet.pet_id for g in self.graveyard):
            logger.warning(f"Pet {pet.name} [{pet.pet_id}] already in graveyard, skipping.")
            return next(g for g in self.graveyard if g["id"] == pet.pet_id)

        entry = pet.to_graveyard_dict()
        self.graveyard.append(entry)
        self.save_graveyard()
        logger.info(f"Pet '{pet.name}' added to graveyard. Lifespan: {entry['lifespan_display']}")
        return entry

    def save(self) -> None:
        """Save all pet data to config.json and pet_state.json."""
        config_data = {"pets": [p.to_config_dict() for p in self.pets.values()]}
        state_data  = {"pets": [p.to_persistence_dict() for p in self.pets.values()]}

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save persistence: {e}")

    def adopt_pet(self, name: str, species: str, gender: str, owner: str = "unknown") -> dict:
        """Adopt a new pet. Only one pet allowed at a time. Returns result dict."""
        # ─── Single pet limit ─────────────────────────────────────────
        if self.pets:
            existing = list(self.pets.values())[0]
            if not existing.is_dead:
                return {
                    "success": False,
                    "error": f"You already have a pet: {existing.name}. "
                             f"Only one pet is allowed at a time.",
                }
            else:
                # Previous pet died — archive to graveyard and remove
                logger.info(f"Archiving deceased pet '{existing.name}' to graveyard.")
                graveyard_entry = self.add_to_graveyard(existing)
                del self.pets[existing.pet_id]

        # Validate species
        valid_species = [s["id"] for s in ADOPTABLE_SPECIES]
        if species not in valid_species:
            return {
                "success": False,
                "error": f"Unknown species '{species}'. Valid: {valid_species}",
            }

        # Validate gender
        if gender not in ("male", "female"):
            return {"success": False, "error": "Gender must be 'male' or 'female'."}

        # Create new pet
        pet_id = str(uuid.uuid4())[:8]
        pet = PetData(pet_id=pet_id, name=name, species=species, gender=gender, owner=owner)
        pet.signature = pet.generate_signature(self.secret_key)
        self.pets[pet_id] = pet

        logger.info(f"New pet adopted: {name} ({species}, {gender}) -> [{pet_id}]")
        self.save()

        # Reset owner info for the new cat's fresh start
        from history_manager import HistoryManager
        hm = HistoryManager()
        hm.reset_owner_info()

        # Broadcast update asynchronously
        asyncio.create_task(ws_manager.broadcast_pets_state(self))

        # Build the adoption response
        result = {
            "success": True,
            "pet_id": pet_id,
            "message": f"Successfully adopted {name}!",
            "pet": pet.to_config_dict(),
        }

        # If there are deceased pets, include motivational report
        if self.graveyard:
            deceased_reports = []
            for g in self.graveyard:
                deceased_reports.append(
                    f"🐾 {g['name']} ({g['species_name']}) - "
                    f"存活了 {g['lifespan_display']}"
                )
            result["previous_pets_report"] = {
                "total_deceased": len(self.graveyard),
                "deceased_list": deceased_reports,
                "motivation": (
                    f"⚠️ 你已经有 {len(self.graveyard)} 只猫咪离世了。"
                    f"请记住它们的名字，用心照顾这只新猫咪，不要再让悲剧重演！"
                ),
            }

        return result

    def tick(self) -> None:
        """Run one state evolution tick for all pets."""
        for pet in self.pets.values():
            pet.apply_decay()
            pet.compute_state()
            pet.last_updated = time.time()
        self.save()


# ─── Background Task ─────────────────────────────────────────────────────────
async def state_evolution_loop(manager: PetManager, interval: int):
    """Background loop that evolves pet states periodically."""
    logger.info(f"State evolution loop started (interval: {interval}s)")
    
    from llm_client import LLMClient, load_llm_config
    from history_manager import HistoryManager
    import subprocess
    import random
    
    llm = LLMClient()
    hm = HistoryManager()
    cfg = load_llm_config()
    cmd_template = cfg.get("notification_cmd", ["python", "scripts/send_notify.py", "{content}"])
    
    while True:
        try:
            await asyncio.sleep(interval)
            if manager.pets:
                manager.tick()
                pet_summary = ", ".join(
                    f"{p.name}:{p.current_state.value}" for p in manager.pets.values()
                )
                logger.debug(f"Tick complete. States: [{pet_summary}]")
                
                # Proactive Checks
                now = time.time()
                for pet in list(manager.pets.values()):
                    if pet.is_dead:
                        continue
                        
                    trigger_event = None
                    if pet.hunger < 20:
                        trigger_event = "I feel very hungry."
                    elif pet.thirst < 20:
                        trigger_event = "I feel very thirsty."
                    elif pet.happiness < 20:
                        trigger_event = "I feel very unhappy."
                    elif pet.health < 30:
                        trigger_event = "I feel sick."
                    elif now > pet.next_proactive_check:
                        trigger_event = "I'm bored and want to poke my owner."

                    if trigger_event and now > pet.next_proactive_check:
                        pet.next_proactive_check = now + random.uniform(600, 1800)
                        
                        async def do_poke(p, event):
                            try:
                                reply = await llm.chat(
                                    pet_state=p.to_state_dict(),
                                    traits=p.to_state_dict().get("traits_desc", []),
                                    memory_summary=hm.get_summary(p.pet_id),
                                    recent_history=hm.get_recent_history(p.pet_id),
                                    trigger_event=event
                                )
                                if reply:
                                    p.current_speech = reply
                                    hm.append_message(p.pet_id, "assistant", reply)
                                    p.add_notification(reply)
                                    
                                    try:
                                        cmd = [arg.replace("{content}", reply) for arg in cmd_template]
                                        subprocess.run(cmd, shell=False)
                                    except Exception as e:
                                        logger.error(f"Failed to run notification_cmd: {e}")
                                        
                                    manager.save()
                                    await ws_manager.broadcast_pets_state(manager)
                            except Exception as e:
                                logger.error(f"Proactive poke failed: {e}")

                        asyncio.create_task(do_poke(pet, trigger_event))

                # Broadcast updated states to all connected WS clients
                await ws_manager.broadcast_pets_state(manager)
        except asyncio.CancelledError:
            logger.info("State evolution loop cancelled. Saving final state...")
            manager.save()
            break
        except Exception as e:
            logger.error(f"Error in state evolution loop: {e}", exc_info=True)


# ─── Lifespan ─────────────────────────────────────────────────────────────────
_background_task = None

@asynccontextmanager
async def lifespan(app):
    """FastAPI lifespan: start background task on startup, stop on shutdown."""
    global _background_task
    # Access the manager stored on the app state
    manager = app.state.pet_manager
    interval = app.state.tick_interval

    _background_task = asyncio.create_task(
        state_evolution_loop(manager, interval)
    )
    logger.info("OpenCat backend started.")
    yield
    # Shutdown
    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass
    manager.save()
    logger.info("OpenCat backend shut down gracefully.")


# ─── Signal Handling ──────────────────────────────────────────────────────────
def setup_signal_handlers(manager: PetManager):
    """Register signal handlers for graceful shutdown."""
    def on_signal(signum, frame):
        logger.info(f"Received signal {signum}. Saving state and exiting...")
        manager.save()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="OpenCat Backend Service")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Host to bind to (default: {DEFAULT_HOST}). Use 0.0.0.0 for headless server.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT}).")
    parser.add_argument("--tick-interval", type=int, default=DEFAULT_TICK_INTERVAL,
                        help=f"Seconds between state evolution ticks (default: {DEFAULT_TICK_INTERVAL}).")
    return parser.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()
    setup_logging()

    logger.info("=" * 60)
    logger.info("  OpenCat Backend Service Starting")
    logger.info(f"  Host: {args.host}:{args.port}")
    logger.info(f"  Tick Interval: {args.tick_interval}s")
    logger.info(f"  Config: {CONFIG_FILE}")
    logger.info(f"  State:  {STATE_FILE}")
    logger.info("=" * 60)

    # Initialize manager and load persisted data
    manager = PetManager()
    manager.load()
    setup_signal_handlers(manager)

    # Create FastAPI app
    app = create_app(manager)
    app.router.lifespan_context = lifespan
    app.state.pet_manager = manager
    app.state.tick_interval = args.tick_interval

    # Run the server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
