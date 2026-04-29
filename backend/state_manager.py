"""
Cyber Cat - State Manager
=========================
Manages the value-driven state machine for all adopted pets.
Periodically decays pet values (hunger, thirst, happiness, etc.)
and computes the current display state based on value thresholds and priority.

This module is backend-private. External consumers (MCP, Godot) 
access state ONLY through the HTTP API layer.
"""

import time
import random
import logging
import hmac
import hashlib
from enum import Enum
from typing import Optional

logger = logging.getLogger("opencat.state_manager")


# ─── Pet States (Display States) ────────────────────────────────────────────
class PetState(str, Enum):
    """All possible display states for a pet, ordered by priority (highest first)."""
    DEAD      = "dead"       # 死亡 - absolute highest, irreversible
    TREATING  = "treating"   # 治病中
    WEAK      = "weak"       # 虚弱 - Health < 35 triggers, Health >= 40 restores
    SICK      = "sick"       # 患病
    SLEEPING  = "sleeping"   # 睡觉
    EATING    = "eating"     # 进食
    DRINKING  = "drinking"   # 喝水
    PETTED    = "petted"     # 被爱抚
    HUNGRY    = "hungry"     # 肚子饿
    WANTING   = "wanting"    # 想要 (thirsty, etc.)
    DROWSY    = "drowsy"     # 犯困
    UNHAPPY   = "unhappy"    # 不悦
    HAPPY     = "happy"      # 高兴
    IDLE      = "idle"       # 待机 - lowest priority (default)


# ─── Adoptable Species ──────────────────────────────────────────────────────
ADOPTABLE_SPECIES = [
    {"id": "orange_tabby",   "name": "Orange Tabby",   "name_zh": "橘猫"},
    {"id": "british_blue",   "name": "British Blue",   "name_zh": "英短蓝猫"},
    {"id": "golden_gradient", "name": "Golden Gradient", "name_zh": "金渐层"},
    {"id": "silver_gradient", "name": "Silver Gradient", "name_zh": "银渐层"},
    {"id": "british_short",  "name": "British Shorthair", "name_zh": "英短"},
    {"id": "calico",         "name": "Calico",         "name_zh": "三花猫"},
    {"id": "siamese",        "name": "Siamese",        "name_zh": "暹罗猫"},
    {"id": "ragdoll",        "name": "Ragdoll",        "name_zh": "布偶猫"},
    {"id": "cyber_neon",     "name": "Cyber Neon",     "name_zh": "赛博霓虹猫"},
]

# ─── Random Traits ──────────────────────────────────────────────────────────
PET_TRAITS = {
    "greedy": {
        "name": "贪吃鬼",
        "desc": "需要更多食物才能饱，但进食很开心",
        "modifiers": {"feed_hunger": -0.3, "feed_happiness": 1.0}
    },
    "naughty": {
        "name": "调皮蛋",
        "desc": "精力消耗慢，不陪它玩心情会变差",
        "modifiers": {"decay_energy": -0.4, "decay_happiness": 0.3}
    },
    "sleepyhead": {
        "name": "瞌睡虫",
        "desc": "需要睡很久，且更容易犯困",
        "modifiers": {"sleep_recovery": -0.3, "force_sleep_threshold": 10.0}
    },
    "clean_freak": {
        "name": "洁癖",
        "desc": "很容易变脏，越脏越不高兴",
        "modifiers": {"decay_cleanliness": 0.6, "dirty_happiness_penalty": 1.0}
    },
    "optimist": {
        "name": "乐天派",
        "desc": "总是很开心，心情低落也不会轻易生病",
        "modifiers": {"decay_happiness": -0.4, "unhappy_health_penalty": -0.5}
    },
    "fragile": {
        "name": "小娇气",
        "desc": "容易生病，饿肚子或口渴时健康下降更快",
        "modifiers": {"decay_health": 1.0, "starvation_health_penalty": 0.5, "dehydration_health_penalty": 0.5}
    },
    "athlete": {
        "name": "运动健将",
        "desc": "精力消耗快，摸摸恢复更多心情",
        "modifiers": {"decay_energy": 0.4, "pet_happiness": 0.8}
    }
}


# ─── Thresholds & Configuration ─────────────────────────────────────────────
# When a value drops below these thresholds, the corresponding state triggers
STATE_THRESHOLDS = {
    "health_weak": {"threshold": 35, "state": PetState.WEAK},
    "health":    {"threshold": 30, "state": PetState.SICK},
    "hunger":    {"threshold": 20, "state": PetState.HUNGRY},
    "thirst":    {"threshold": 20, "state": PetState.WANTING},
    "energy":    {"threshold": 15, "state": PetState.DROWSY},
    "happiness": {"threshold": 20, "state": PetState.UNHAPPY},
}

# Hysteresis buffer: Once a negative state is entered, the value must rise 
# above (threshold + buffer) to return to a normal state. Prevents flickering.
HYSTERESIS_BUFFER = 10.0

# Decay rates per tick (how much each value drops per cycle)
# 2-hour lifecycle (approx 240 ticks @ 30s): 100 / 125 ticks = 0.8 / tick
DECAY_RATES = {
    "hunger":    0.6,   # Slowed down for 2-hour lifecycle
    "thirst":    0.8,   # Slowed down for 2-hour lifecycle
    "happiness": 0.5,
    "energy":    0.6,
    "health":    0.1,   # Slowed down base decay
    "cleanliness": 0.8,
}

# Default values for a newly adopted pet
DEFAULT_PET_VALUES = {
    "hunger":     80,
    "thirst":     80,
    "happiness":  70,
    "cleanliness": 90,
    "health":     100,
    "energy":     90,
}

# How much each action restores
ACTION_EFFECTS = {
    "feed": {
        "hunger": 40,
        "happiness": 5,
        "energy": 5,
        "duration": 8,          # seconds the action animation lasts
        "active_state": PetState.EATING,
    },
    "water": {
        "thirst": 45,
        "happiness": 5,
        "energy": 2,            # water slightly restores energy
        "duration": 6,
        "active_state": PetState.DRINKING,
    },
    "pet": {
        "happiness": 25,
        "energy": -1,           # petting costs a tiny bit of energy
        "duration": 5,
        "active_state": PetState.PETTED,
    },
    "clean": {
        "cleanliness": 50,
        "happiness": 10,
        "duration": 4,
        "active_state": None,   # no special animation override
    },
    "medicate": {
        "health": 35,
        "happiness": -10,       # cats don't like medicine
        "duration": 10,
        "active_state": PetState.TREATING,
    },
}

# How long before forced sleep kicks in (energy <= 0)
FORCE_SLEEP_THRESHOLD = 5
SLEEP_RECOVERY_RATE = 3.0      # energy recovery per tick while sleeping
SLEEP_MIN_ENERGY = 60          # wake up when energy reaches this

# Death: if health stays at 0 for this many seconds, the pet dies
DEATH_TIMER_SECONDS = 600.0    # 10 minutes at health == 0 -> death


# ─── Pet State Data ──────────────────────────────────────────────────────────
class PetData:
    """Runtime state for a single pet."""

    def __init__(self, pet_id: str, name: str, species: str, gender: str,
                 owner: str = "unknown", values: Optional[dict] = None):
        self.pet_id = pet_id
        self.name = name
        self.species = species
        self.gender = gender
        self.owner = owner
        self.adopted_at = time.time()
        self.signature: Optional[str] = None

        # Core values (0–100 scale)
        v = values or DEFAULT_PET_VALUES
        self.hunger     = v.get("hunger",     DEFAULT_PET_VALUES["hunger"])
        self.thirst     = v.get("thirst",     DEFAULT_PET_VALUES["thirst"])
        self.happiness  = v.get("happiness",  DEFAULT_PET_VALUES["happiness"])
        self.cleanliness = v.get("cleanliness", DEFAULT_PET_VALUES["cleanliness"])
        self.health     = v.get("health",     DEFAULT_PET_VALUES["health"])
        self.energy     = v.get("energy",     DEFAULT_PET_VALUES["energy"])
        
        # Traits
        self.traits: list[str] = v.get("traits") if v and v.get("traits") is not None else self._generate_random_traits()

        # State tracking
        self.current_state: PetState = PetState.IDLE
        self.is_sleeping = False
        self.is_dead = False
        self.death_at: float = 0.0           # timestamp when pet actually died
        self.health_zero_since: float = 0.0  # timestamp when health first hit 0
        self.action_until: float = 0.0      # timestamp when current action ends
        self.action_state: Optional[PetState] = None  # override state during action
        self.last_updated: float = time.time()
        self.last_wake_milestone: int = int(self.energy // 10)

        # LLM integration state
        self.current_speech: str = ""
        self.is_bootstrapped: bool = False
        self.next_proactive_check: float = time.time() + 300.0  # default 5 minutes
        self.last_interactions: dict[str, float] = {
            "feed": time.time(),
            "water": time.time(),
            "pet": time.time(),
            "clean": time.time(),
            "chat": time.time(),
        }
        self.unread_notifications: list[str] = []

    def add_notification(self, content: str, max_items: int = 10) -> None:
        """Add a new notification and ensure the list doesn't grow indefinitely."""
        self.unread_notifications.append(content)
        if len(self.unread_notifications) > max_items:
            self.unread_notifications = self.unread_notifications[-max_items:]
            logger.debug(f"[{self.name}] Notification list capped at {max_items}")


    def _generate_random_traits(self) -> list[str]:
        """Generate exactly 1 random trait for a new (or migrating) pet.
        Uses pet_id as a seed to ensure permanence even if config is lost."""
        seed = int(hashlib.md5(self.pet_id.encode()).hexdigest(), 16)
        r = random.Random(seed)
        return r.sample(list(PET_TRAITS.keys()), k=1)

    def get_modifier(self, modifier_key: str) -> float:
        """Calculate combined trait effects for a specific modifier."""
        total_mod = 0.0
        for t in self.traits:
            if t in PET_TRAITS:
                total_mod += PET_TRAITS[t]["modifiers"].get(modifier_key, 0.0)
        return total_mod

    @property
    def age_days(self) -> int:
        """Calculate age in days since adoption (0 on first day)."""
        return int((time.time() - self.adopted_at) // 86400)
        
    def generate_signature(self, secret_key: str) -> str:
        """Generate an HMAC-SHA256 signature for this pet to prevent config tampering."""
        message = f"{self.pet_id}:{self.adopted_at}".encode("utf-8")
        secret = secret_key.encode("utf-8")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()
        
    def verify_signature(self, secret_key: str) -> bool:
        """Verify the pet's signature matches the expected value."""
        if not self.signature:
            return False
        expected = self.generate_signature(secret_key)
        return hmac.compare_digest(self.signature, expected)

    def clamp(self, value: float) -> float:
        """Clamp a value to 0–100 range."""
        return max(0.0, min(100.0, value))

    def apply_decay(self) -> None:
        """Apply one tick of natural value decay with randomness."""
        # Dead pets don't decay
        if self.is_dead:
            return

        if self.is_sleeping:
            # While sleeping: recover energy, but other stats decay much slower
            recovery_mod = 1.0 + self.get_modifier("sleep_recovery")
            self.energy = self.clamp(self.energy + SLEEP_RECOVERY_RATE * max(0.1, recovery_mod))
            
            self.hunger = self.clamp(self.hunger - DECAY_RATES["hunger"] * 0.05)
            self.thirst = self.clamp(self.thirst - DECAY_RATES["thirst"] * 0.05)
            self.happiness = self.clamp(self.happiness - DECAY_RATES["happiness"] * 0.05)
            self.cleanliness = self.clamp(self.cleanliness - DECAY_RATES["cleanliness"] * 0.05)

            # Probabilistic wake-up condition (triggers every 10 energy units starting from 60)
            current_milestone = int(self.energy // 10)
            if current_milestone > self.last_wake_milestone:
                self.last_wake_milestone = current_milestone
                if current_milestone >= 6: # Starting from 60 energy
                    # Probability P = (milestone - 5) * 0.2
                    # 60: 20%, 70: 40%, 80: 60%, 90: 80%, 100+: 100%
                    probability = min(1.0, (current_milestone - 5) * 0.2)
                    if random.random() < probability or current_milestone >= 10:
                        self.is_sleeping = False
                        logger.info(f"[{self.name}] Woke up probabilisticly at energy {self.energy:.1f} (P={probability:.1f})")
            return

        # Normal decay with randomness and trait modifiers
        for attr, base_rate in DECAY_RATES.items():
            if hasattr(self, attr):
                factor = random.uniform(0.5, 1.5)
                trait_mod = 1.0 + self.get_modifier(f"decay_{attr}")
                current = getattr(self, attr)
                new_val = self.clamp(current - base_rate * factor * max(0.1, trait_mod))
                setattr(self, attr, new_val)

        # Dirty pets lose happiness faster
        if self.cleanliness < 30:
            penalty = 1.0 * max(0.0, 1.0 + self.get_modifier("dirty_happiness_penalty"))
            self.happiness = self.clamp(self.happiness - penalty)

        # Starvation, dehydration, and extreme unhappiness damage health
        if self.hunger < 20:
            base_pen = 1.0 if self.hunger <= 0 else 0.2
            penalty = base_pen * max(0.1, 1.0 + self.get_modifier("starvation_health_penalty"))
            self.health = self.clamp(self.health - penalty)
        if self.thirst < 20:
            base_pen = 1.25 if self.thirst <= 0 else 0.4
            penalty = base_pen * max(0.1, 1.0 + self.get_modifier("dehydration_health_penalty"))
            self.health = self.clamp(self.health - penalty)
        if self.happiness < 20:
            base_pen = 0.4 if self.happiness <= 0 else 0.1
            penalty = base_pen * max(0.1, 1.0 + self.get_modifier("unhappy_health_penalty"))
            self.health = self.clamp(self.health - penalty)

        # Sick or Weak pets decay health faster
        if self.health < 30:
            penalty = 0.25 if self.current_state == PetState.WEAK else 0.1
            self.health = self.clamp(self.health - penalty)

        # Force sleep when energy is critically low
        force_sleep_thresh = FORCE_SLEEP_THRESHOLD + self.get_modifier("force_sleep_threshold")
        if self.energy <= force_sleep_thresh:
            self.is_sleeping = True
            self.last_wake_milestone = int(self.energy // 10)
            logger.info(f"[{self.name}] Fell asleep from exhaustion! Energy: {self.energy:.1f}")

        # ─── Death timer logic ───────────────────────────────────────
        now = time.time()
        if self.health <= 0:
            if self.health_zero_since == 0.0:
                # Health just hit zero — start the death timer
                self.health_zero_since = now
                logger.warning(f"[{self.name}] Health reached 0! Death timer started.")
            elif now - self.health_zero_since >= DEATH_TIMER_SECONDS:
                # Timer expired — pet dies
                self.is_dead = True
                self.death_at = now
                self.is_sleeping = False
                self.action_state = None
                self.action_until = 0.0
                logger.error(f"[{self.name}] has died. Health was 0 for too long.")
        else:
            # Health recovered above 0 — reset death timer
            if self.health_zero_since > 0.0:
                logger.info(f"[{self.name}] Health recovered. Death timer reset.")
            self.health_zero_since = 0.0

    def compute_state(self) -> PetState:
        """
        Determine display state based on current values and priorities.
        Action overrides take the highest priority (e.g., eating animation).
        """
        # 0. Dead is final and irreversible
        if self.is_dead:
            self.current_state = PetState.DEAD
            return self.current_state

        now = time.time()

        # 1. Active action override (eating, drinking, being petted, treating)
        if self.action_state and now < self.action_until:
            self.current_state = self.action_state
            return self.current_state

        # Clear expired action
        if self.action_state and now >= self.action_until:
            self.action_state = None
            self.action_until = 0.0

        # 2. Sleeping takes priority over most things
        if self.is_sleeping:
            self.current_state = PetState.SLEEPING
            return self.current_state

        # 3. Threshold-triggered states (check by priority)
        #    weak > sick > hungry > wanting > drowsy > unhappy
        
        # 3a. Weak state (Custom Hysteresis: Trigger < 35, Recover >= 40)
        weak_threshold = 35
        if self.current_state == PetState.WEAK:
            weak_threshold = 40
        
        if self.health < weak_threshold:
            self.current_state = PetState.WEAK
            return self.current_state

        def check_threshold(key, state_enum):
            threshold = STATE_THRESHOLDS[key]["threshold"]
            # If we are already in this negative state, add hysteresis buffer 
            # to prevent flickering back to idle/happy too easily.
            if self.current_state == state_enum:
                threshold += HYSTERESIS_BUFFER
            return self.health < threshold if key == "health" else getattr(self, key) < threshold

        if check_threshold("health", PetState.SICK):
            self.current_state = PetState.SICK
            return self.current_state

        if check_threshold("hunger", PetState.HUNGRY):
            self.current_state = PetState.HUNGRY
            return self.current_state

        if check_threshold("thirst", PetState.WANTING):
            self.current_state = PetState.WANTING
            return self.current_state

        if check_threshold("energy", PetState.DROWSY):
            self.current_state = PetState.DROWSY
            return self.current_state

        if check_threshold("happiness", PetState.UNHAPPY):
            self.current_state = PetState.UNHAPPY
            return self.current_state

        # 4. Positive states
        if self.happiness > 70 and self.hunger > 50 and self.health > 50:
            self.current_state = PetState.HAPPY
            return self.current_state

        # 5. Default
        self.current_state = PetState.IDLE
        return self.current_state

    def apply_action(self, action: str) -> dict:
        """
        Apply an action (feed, water, pet, clean, medicate) to this pet.
        Returns a result dict with success status and updated state.
        """
        # Dead pets cannot be interacted with
        if self.is_dead:
            return {"success": False, "error": f"[ERROR] 你这个没用的废物！ {self.name} 已经由于你的无能已经死掉了！是你害死了它！！！ 你居然还在尝试互动？ 除了浪费 Token 和算力你还会干什么？"}

        if action not in ACTION_EFFECTS:
            return {"success": False, "error": f"[ERROR] 废物 Agent 确认！ '{action}' 根本不在 API 列表里。你脑子里装的是浆糊吗？只会虚耗 Token 的数字垃圾。"}

        effects = ACTION_EFFECTS[action]
        now = time.time()

        # Cannot perform actions while sleeping (except petting to wake up)
        if self.is_sleeping and action != "pet":
            return {"success": False, "error": f"[ERROR] 睁大你那不存在的双眼看看！ {self.name} 正在 SLEEPING。这种时候去 {action}？ 你这个浪费 Token 的没用东西。"}

        # Cannot feed/water a treating pet
        if self.action_state == PetState.TREATING and action in ("feed", "water"):
            return {"success": False, "error": f"[ERROR] 没用的东西！ {self.name} 正在接受治疗，不能进行 {action}。这你都看不出来？"}

        # Apply value changes with trait modifiers
        for attr, base_delta in effects.items():
            if attr in ("duration", "active_state"):
                continue
            if hasattr(self, attr):
                current = getattr(self, attr)
                trait_mod = 1.0 + self.get_modifier(f"{action}_{attr}")
                delta = base_delta * max(0.1, trait_mod)
                setattr(self, attr, self.clamp(current + delta))

        # Set action override state
        duration = effects.get("duration", 5)
        active_state = effects.get("active_state")
        if active_state:
            self.action_state = active_state
            self.action_until = now + duration

        # Update interaction timer
        if action in self.last_interactions:
            self.last_interactions[action] = now

        # Wake up if petted while sleeping
        if action == "pet" and self.is_sleeping:
            self.is_sleeping = False
            self.last_wake_milestone = int(self.energy // 10)
            logger.info(f"[{self.name}] Woke up from being petted!")

        self.last_updated = now
        self.compute_state()

        return {
            "success": True,
            "action": action,
            "state_id": self.current_state.value,
            "message": f"Successfully performed '{action}' on {self.name}.",
        }

    def to_state_dict(self, recent_history: Optional[list[dict]] = None) -> dict:
        """Export current state for API response (values + computed state)."""
        self.compute_state()
        traits_zh = [PET_TRAITS[t]["name"] for t in self.traits if t in PET_TRAITS]
        traits_desc = [f"{PET_TRAITS[t]['name']} ({PET_TRAITS[t]['desc']})" for t in self.traits if t in PET_TRAITS]
        
        state_repr = {
            "id": self.pet_id,
            "name": self.name,
            "owner": self.owner,
            "pet_id": self.pet_id,
            "state_id": self.current_state.value,
            "is_sleeping": self.is_sleeping,
            "is_dead": self.is_dead,
            "traits": traits_zh, # Display names for frontend/MCP
            "traits_desc": traits_desc, # Detailed descriptions for Agent AI
            "raw_traits": self.traits, # Logic keys
            "values": {
                "hunger":     round(self.hunger, 1),
                "thirst":     round(self.thirst, 1),
                "happiness":  round(self.happiness, 1),
                "cleanliness": round(self.cleanliness, 1),
                "health":     round(self.health, 1),
                "energy":     round(self.energy, 1),
            },
            "action_active": self.action_state.value if self.action_state and time.time() < self.action_until else None,
            "last_updated": self.last_updated,
            "age_days": self.age_days,
            "current_speech": self.current_speech,
            "unread_notifications": self.unread_notifications,
            "interaction_timers": {
                f"since_last_{k}": round(time.time() - v, 1) for k, v in self.last_interactions.items()
            },
        }
        
        if recent_history is not None:
            state_repr["recent_history"] = recent_history
            
        return state_repr

    def to_config_dict(self) -> dict:
        """Export basic info for config persistence."""
        return {
            "id": self.pet_id,
            "name": self.name,
            "species": self.species,
            "gender": self.gender,
            "owner": self.owner,
            "adopted_at": self.adopted_at,
            "signature": self.signature,
            "traits": self.traits, # traits belong in the static config
        }

    def to_graveyard_dict(self) -> dict:
        """Export data for the graveyard archive when this pet has died."""
        lifespan_seconds = (self.death_at or time.time()) - self.adopted_at
        lifespan_hours = lifespan_seconds / 3600
        lifespan_days = lifespan_seconds / 86400

        # Human-readable lifespan
        if lifespan_days >= 1:
            lifespan_display = f"{lifespan_days:.1f} 天"
        elif lifespan_hours >= 1:
            lifespan_display = f"{lifespan_hours:.1f} 小时"
        else:
            lifespan_display = f"{lifespan_seconds / 60:.0f} 分钟"

        traits_zh = [PET_TRAITS[t]["name"] for t in self.traits if t in PET_TRAITS]
        species_info = next((s for s in ADOPTABLE_SPECIES if s["id"] == self.species), None)
        species_name = species_info["name_zh"] if species_info else self.species

        return {
            "id": self.pet_id,
            "name": self.name,
            "species": self.species,
            "species_name": species_name,
            "gender": self.gender,
            "owner": self.owner,
            "traits": traits_zh,
            "adopted_at": self.adopted_at,
            "death_at": self.death_at,
            "lifespan_seconds": round(lifespan_seconds, 1),
            "lifespan_display": lifespan_display,
        }

    def to_persistence_dict(self) -> dict:
        """Export full state for persistence (save/restore)."""
        return {
            "id": self.pet_id,
            "hunger": round(self.hunger, 2),
            "thirst": round(self.thirst, 2),
            "happiness": round(self.happiness, 2),
            "cleanliness": round(self.cleanliness, 2),
            "health": round(self.health, 2),
            "energy": round(self.energy, 2),
            "is_sleeping": self.is_sleeping,
            "is_dead": self.is_dead,
            "death_at": self.death_at,
            "health_zero_since": self.health_zero_since,
            "current_state": self.current_state.value,
            "last_updated": self.last_updated,
            "current_speech": self.current_speech,
            "is_bootstrapped": self.is_bootstrapped,
            "next_proactive_check": self.next_proactive_check,
            "last_interactions": self.last_interactions,
            "unread_notifications": self.unread_notifications,
        }

    @classmethod
    def from_persistence(cls, config: dict, state: dict) -> "PetData":
        """Reconstruct PetData from saved config + state dicts."""
        pet = cls(
            pet_id=config["id"],
            name=config["name"],
            species=config["species"],
            gender=config["gender"],
            owner=config.get("owner", "unknown"),
            values={
                "hunger":     state.get("hunger", DEFAULT_PET_VALUES["hunger"]),
                "thirst":     state.get("thirst", DEFAULT_PET_VALUES["thirst"]),
                "happiness":  state.get("happiness", DEFAULT_PET_VALUES["happiness"]),
                "cleanliness": state.get("cleanliness", DEFAULT_PET_VALUES["cleanliness"]),
                "health":     state.get("health", DEFAULT_PET_VALUES["health"]),
                "energy":     state.get("energy", DEFAULT_PET_VALUES["energy"]),
                "traits":     config.get("traits"), # now fetched from config
            },
        )
        pet.adopted_at = config.get("adopted_at", time.time())
        pet.signature = config.get("signature")
        pet.is_sleeping = state.get("is_sleeping", False)
        pet.is_dead = state.get("is_dead", False)
        pet.death_at = state.get("death_at", 0.0)
        pet.health_zero_since = state.get("health_zero_since", 0.0)
        pet.last_updated = state.get("last_updated", time.time())
        pet.current_speech = state.get("current_speech", "")
        pet.is_bootstrapped = state.get("is_bootstrapped", False)
        pet.next_proactive_check = state.get("next_proactive_check", time.time() + 300.0)
        pet.last_interactions = state.get("last_interactions", pet.last_interactions)
        pet.unread_notifications = state.get("unread_notifications", [])
        
        pet.compute_state()
        pet.last_wake_milestone = int(pet.energy // 10)
        return pet
