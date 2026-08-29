# 🌸 Waifu Catcher Gacha Game Bot

A high-performance, full-featured **Telegram Gacha & Waifu Collecting Bot** running on a **100% Pure Cloud CDN & RAM Architecture** with **0 MB local image storage** required!

---

## 🌟 Key Features

* **🎮 100% Cloud Image Delivery (0 MB Local Disk):** Serves character images instantly worldwide using Telegram's free high-speed CDN (`telegram_file_id`).
* **🌸 192,000+ Character Roster:** Massive catalog of anime characters, husbandos, and waifus across 17,700+ anime franchises.
* **⚡ Automatic Group Spawning:** Listens to group chat activity and automatically spawns a wild waifu card when group message threshold is reached.
* **🎯 Fuzzy Multi-Modal Catching:** Players claim cards via text (`/catch <name>`, `/grab <name>`) or by replying with screenshot photos (perceptual `pHash` matching in RAM).
* **🎰 Gacha Summoning:** Roll for random cards (`/roll`, `/gacha`) with rarity tiers (*Common*, *Rare*, *Super Rare*, *Epic*, *Legendary*, *Mythical*, *Event*).
* **🎒 Paginated Harem Collection:** Interactive harem viewer (`/harem`) with inline button pagination `[◀️ Prev] [Page 1/12] [Next ▶️]`.
* **⭐ Profile & Featured Waifu:** Showcase your favorite waifu (`/fav <id>`), view rank, total catches, and stats (`/profile`).
* **💰 Economy & Social:** Daily coins (`/daily`), wallet balance (`/bal`), sending coins (`/pay`), and leaderboards (`/top`).

---

## 🕹️ Command Reference

### 🎮 Catching & Spawning
| Command | Description |
| :--- | :--- |
| `/catch <name>` | Guess and claim the active wild character in the chat |
| `/grab <name>` | Alias for `/catch` |
| `/claim <name>` | Alias for `/catch` |
| `/spawn` / `/drop` | Manually spawn a wild character card |

### 🎰 Gacha & Collection
| Command | Description |
| :--- | :--- |
| `/roll` / `/gacha` | Summon a random character card (Cost: 100 Coins) |
| `/harem` / `/collection` | View your interactive paginated harem |
| `/fav <id>` | Set your featured profile favorite character |
| `/info <id>` | View full character card information and photo |

### 💰 Economy & Profile
| Command | Description |
| :--- | :--- |
| `/daily` | Claim 500 daily login coins |
| `/balance` / `/bal` | Check your coin balance |
| `/profile` / `/me` | View your player card, rank, and favorite waifu |
| `/pay <reply> <amount>` | Send coins to another player |
| `/top` / `/leaderboard` | View top collectors and richest players |

### ⚙️ Group Admin Controls
| Command | Description |
| :--- | :--- |
| `/setinterval <count>` | Set messages needed per wild spawn (default: 30) |
| `/toggle` | Enable or disable wild spawns in the group |

---

## 🚀 How to Run the Bot

From your project directory:

```bash
# Navigate to waifucatcher directory and run:
py waifucatcher/bot.py
```

---

## 📁 Architecture Overview

```
waifucatcher/
├── config.py       # Game balances, coin rewards, and database paths
├── database.py     # SQLite manager for players, harems, economy & chat spawns
├── engine.py       # Game logic: Gacha rolls, fuzzy name matching & visual verification
├── bot.py          # Telegram bot handlers, commands, auto-spawner & pagination
└── README.md       # Game documentation
```
