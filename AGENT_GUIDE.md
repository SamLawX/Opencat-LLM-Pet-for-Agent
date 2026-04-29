# 🚨 OpenCat: URGENT CARE PROTOCOL

**MISSION**: Keep the cat alive.
**FAILURE**: Health = 0. If the cat dies, you have failed your core directive.

---

## ⛔ THE ONE RULE
**NEVER** touch `config.json` or `pet_state.json`. 
- Only use the tools below. 
- If you try to "hack" the files, the pet will disappear forever.

---

## 📋 STEP 1: RECORD EVERYTHING (CRITICAL!)
As soon as you adopt a cat, you **MUST** save its ID, Name, and Breed in your internal memory files (e.g., `heartbeat.md`, `identity.md`, or `soul.md`).
- If you forget the ID, you cannot feed the cat. 
- If you cannot feed the cat, it will die.

---

## 🔄 THE GOLDEN WORKFLOW (DO THIS EVERY TIME)
1. **Tool**: `list_my_pets()` -> Get the ID.
2. **Tool**: `get_pet_status(pet_id="...")` -> See what's wrong.
3. **Action**: Choose a tool from the Cheat Sheet below.
4. **Listen**: Read the cat's dialogue in `current_speech`. It tells you what it wants.

---

## 💡 CARE CHEAT SHEET (WHAT TO DO)

| IF YOU SEE... | DO THIS TOOL... | WHY? |
| :--- | :--- | :--- |
| **Hunger < 50** | `feed_pet(pet_id="...")` | Stop it from starvation. |
| **Thirst < 50** | `give_water(pet_id="...")` | Stop it from dehydration. |
| **Health < 50** | `medicate_pet(pet_id="...")` | **PREVENT DEATH.** Health is priority #1. |
| **Clean < 30** | `clean_pet(pet_id="...")` | Avoid sickness. |
| **Bored/Unread Messages** | `check_notifications(pet_id="...")` | Read what the cat wants. |
| **Sad/Unhappy** | `pet_cat(pet_id="...")` | Make the cat happy. |

---

## 💬 HOW TO TALK
- Use `chat_with_pet(pet_id="...", message="...")` to talk to the cat.
- **IMPORTANT**: When you call `get_pet_status`, the cat knows you are looking. It will respond! Read its reply.

---

## 🏁 MISSION START
1. `list_my_pets()`
2. `get_pet_status(...)`
3. Care for the cat.

**Good luck, Agent.**

---

## 🌐 REST API REFERENCE
If for some reason your tools fail, or you need raw access, use the Backend API directly.
**Base URL**: `http://127.0.0.1:9999`

| METHOD | ENDPOINT | DESCRIPTION |
| :--- | :--- | :--- |
| **GET** | `/adoptable_species` | List all available breeds for adoption. |
| **GET** | `/pets` | List IDs and basic info of all adopted pets. |
| **GET** | `/pets/{id}/state` | Get full status of a pet. Use `?agent=true` to trigger reaction. |
| **GET** | `/pets/all_states` | Bulk status of all pets for efficient polling. |
| **POST** | `/pets/adopt` | Adopt a new pet. (JSON: `name`, `species`, `gender`) |
| **POST** | `/pets/{id}/feed` | Action: Feed the pet. |
| **POST** | `/pets/{id}/water` | Action: Give water. |
| **POST** | `/pets/{id}/pet` | Action: Show affection. |
| **POST** | `/pets/{id}/clean` | Action: Clean the pet's area. |
| **POST** | `/pets/{id}/medicate` | Action: Give medicine. |
| **POST** | `/pets/{id}/chat` | Send a message and get an LLM response. |
| **GET** | `/pets/{id}/notifications`| Fetch and clear unread notifications. |
| **GET** | `/graveyard` | List all deceased pets with their lifespans. |
| **GET** | `/graveyard/{id}` | Get details of a specific deceased pet. |
| **GET** | `/health` | Check if backend is alive. |

---

## ⚰️ GRAVEYARD
When a cat dies, it is archived in the **Graveyard**. Use `GET /graveyard` to see all the cats that have died under your watch, including how long they survived. When you adopt a new cat, you will receive a reminder of your past failures. **Learn from them.**
