import sqlite3
import discord
from discord.ext import commands
from discord.ui import View, Button
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import os
import re

TOKEN = os.getenv("TOKEN")

# ================================================================
# CONFIG
# ================================================================

ALLOWED_GUILD_ID = 1541164809712697444

OWNERS = {
1393725545853882509,
1235586743991009372,
}

CALL_VOICE_CHANNEL_ID = 1516453482230583458
LOG_CHANNEL_ID = 1516453487322595409
WELCOME_CHANNEL_ID = 1516453500765208596
RULES_CHANNEL_ID = 1516453504955187230
TICKET_PANEL_CHANNEL_ID = 1516453513717219489
TICKET_CATEGORY_ID = 1516453498269597697
SUPPORT_ROLE_ID = 1516453478564630659
BOOST_CHANNEL_ID = 1516453537024966708
AUTO_REACT_CHANNEL_IDS = {1516453525041713225, 1516453552464203806}
ACTIVITY_CHECK_CHANNEL_ID= 1516453525041713225
COUNTING_CHANNEL_ID = 1516453550996324392
AUTO_ROLE_ID = 1516453463188307970
TRIGGER_ROLE_ID = 1516453466921242875
EXTRA_ROLE_ID_1 = 1516453459115638876
EXTRA_ROLE_ID_2 = 1516453459115638876
INVITE_CHANNEL_ID = 1516453511716405421
ROLE_CMD_ALLOWED_ROLE_ID = 1516453466921242875
TIMEOUT_ROLE_ID = 1516453465810014460

VOICE_ALWAYS_ON = True

# ================================================================
# SECURITY CONFIG
# ================================================================

SPAM_MAX_MESSAGES = 5
SPAM_INTERVAL = 3
SPAM_TIMEOUT_SECS = 300
MENTION_MAX = 3
CREATE_MAX = 3
CREATE_INTERVAL = 15

INVITE_PATTERN = re.compile(r"(discord\.gg|discord\.com/invite)/\S+", re.IGNORECASE)

SECURITY_WHITELIST_USERS: set[int] = set()
SECURITY_WHITELIST_ROLES: set[int] = set()

# ================================================================
# BOT SETUP
# ================================================================

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.reactions = True
intents.voice_states = True
intents.guild_messages = True
intents.moderation = True

bot = commands.Bot(
command_prefix=["!", "?"],
intents=intents,
help_command=None,
allowed_mentions=discord.AllowedMentions(
everyone=False, roles=False, users=False, replied_user=False,
),
)

timeout_tracker = defaultdict(list)
kick_tracker = defaultdict(list)
ban_tracker = defaultdict(list)
spam_tracker = defaultdict(list)
mention_tracker = defaultdict(list)
invite_link_tracker = defaultdict(list)
channel_create_tracker = defaultdict(list)
role_create_tracker = defaultdict(list)

counting_state = {"current": 0, "last_user": None, "delete_notice": None}
first_react_announced: set[int] = set()

# ================================================================
# DATABASE
# ================================================================

import pathlib
_DB_PATH = "/data/bot.db" if pathlib.Path("/data").exists() else "bot.db"
if not pathlib.Path("/data").exists():
print("WARNING: /data volume not found — DB will reset on restart!")

_db = sqlite3.connect(_DB_PATH, check_same_thread=False)
_cur = _db.cursor()

_cur.executescript("""
CREATE TABLE IF NOT EXISTS invites (
guild_id INTEGER,
user_id INTEGER,
invites INTEGER DEFAULT 0,
left_invites INTEGER DEFAULT 0,
fake_invites INTEGER DEFAULT 0,
PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS warns (
id INTEGER PRIMARY KEY AUTOINCREMENT,
guild_id INTEGER NOT NULL,
user_id INTEGER NOT NULL,
mod_id INTEGER NOT NULL,
reason TEXT NOT NULL,
timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counting (
guild_id INTEGER PRIMARY KEY,
current INTEGER DEFAULT 0,
last_user INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ticket_counter (
guild_id INTEGER PRIMARY KEY,
counter INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hackbans (
guild_id INTEGER,
user_id INTEGER,
mod_id INTEGER,
reason TEXT,
alts TEXT DEFAULT '',
PRIMARY KEY (guild_id, user_id)
);
""")
_db.commit()

# ── invite helpers ──────────────────────────────────────────────

def _add_invite(guild_id, user_id, amount=1):
_cur.execute("""
INSERT INTO invites (guild_id,user_id,invites) VALUES (?,?,?)
ON CONFLICT(guild_id,user_id) DO UPDATE SET invites=invites+?
""", (guild_id, user_id, amount, amount))
_db.commit()

def _get_invites(guild_id, user_id):
_cur.execute(
"SELECT invites,left_invites,fake_invites FROM invites WHERE guild_id=? AND user_id=?",
(guild_id, user_id)
)
row = _cur.fetchone()
return row if row else (0, 0, 0)

def _set_invites(guild_id, user_id, amount):
_cur.execute("""
INSERT INTO invites (guild_id,user_id,invites) VALUES (?,?,?)
ON CONFLICT(guild_id,user_id) DO UPDATE SET invites=?
""", (guild_id, user_id, amount, amount))
_db.commit()

def _get_top(guild_id, limit=10):
_cur.execute(
"SELECT user_id,invites,left_invites,fake_invites FROM invites WHERE guild_id=? ORDER BY invites DESC LIMIT ?",
(guild_id, limit)
)
return _cur.fetchall()

# ── warn helpers ────────────────────────────────────────────────

def _add_warn(guild_id, user_id, mod_id, reason):
_cur.execute(
"INSERT INTO warns (guild_id,user_id,mod_id,reason,timestamp) VALUES (?,?,?,?,?)",
(guild_id, user_id, mod_id, reason, datetime.utcnow().isoformat())
)
_db.commit()
return _cur.lastrowid

def _get_warns(guild_id, user_id):
_cur.execute(
"SELECT id,mod_id,reason,timestamp FROM warns WHERE guild_id=? AND user_id=? ORDER BY id",
(guild_id, user_id)
)
return _cur.fetchall()

def _del_warn(warn_id, guild_id):
_cur.execute("DELETE FROM warns WHERE id=? AND guild_id=?", (warn_id, guild_id))
_db.commit()
return _cur.rowcount > 0

def _clear_warns(guild_id, user_id):
_cur.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", (guild_id, user_id))
_db.commit()
return _cur.rowcount

# ── counting persistence ────────────────────────────────────────

def _save_count(guild_id, current, last_user):
_cur.execute("""
INSERT INTO counting (guild_id,current,last_user) VALUES (?,?,?)
ON CONFLICT(guild_id) DO UPDATE SET current=?,last_user=?
""", (guild_id, current, last_user, current, last_user))
_db.commit()

def _load_count(guild_id):
_cur.execute("SELECT current,last_user FROM counting WHERE guild_id=?", (guild_id,))
row = _cur.fetchone()
return (row[0], row[1]) if row else (0, 0)

# ── ticket counter ──────────────────────────────────────────────

def _get_ticket_counter(guild_id):
_cur.execute("SELECT counter FROM ticket_counter WHERE guild_id=?", (guild_id,))
row = _cur.fetchone()
return row[0] if row else 0

def _increment_ticket_counter(guild_id):
_cur.execute("""
INSERT INTO ticket_counter (guild_id,counter) VALUES (?,1)
ON CONFLICT(guild_id) DO UPDATE SET counter=counter+1
""", (guild_id,))
_db.commit()
return _get_ticket_counter(guild_id)

# ── hackban helpers ─────────────────────────────────────────────

def _hackban_add(guild_id, user_id, mod_id, reason, alts=""):
_cur.execute("""
INSERT INTO hackbans (guild_id,user_id,mod_id,reason,alts) VALUES (?,?,?,?,?)
ON CONFLICT(guild_id,user_id) DO UPDATE SET mod_id=?,reason=?,alts=?
""", (guild_id, user_id, mod_id, reason, alts, mod_id, reason, alts))
_db.commit()

def _hackban_remove(guild_id, user_id):
_cur.execute("DELETE FROM hackbans WHERE guild_id=? AND user_id=?", (guild_id, user_id))
_db.commit()
return _cur.rowcount > 0

def _hackban_get(guild_id, user_id):
_cur.execute("SELECT mod_id,reason,alts FROM hackbans WHERE guild_id=? AND user_id=?", (guild_id, user_id))
return _cur.fetchone()

def _hackban_all(guild_id):
_cur.execute("SELECT user_id,reason,alts FROM hackbans WHERE guild_id=?", (guild_id,))
return _cur.fetchall()

def _hackban_add_alt(guild_id, user_id, alt_id):
row = _hackban_get(guild_id, user_id)
if not row:
return
alts_str = row[2]
alts = set(alts_str.split(",")) if alts_str else set()
alts.add(str(alt_id))
_cur.execute("UPDATE hackbans SET alts=? WHERE guild_id=? AND user_id=?",
(",".join(filter(None, alts)), guild_id, user_id))
_db.commit()

# ── caches ──────────────────────────────────────────────────────

invite_cache: dict[int, dict[str, int]] = {}
snipe_cache: dict[int, tuple] = {}
afk_users: dict[int, tuple] = {}

# ================================================================
# HELPERS
# ================================================================

def is_owner(user_id):
return user_id in OWNERS

def can_moderate(member):
if member.id in OWNERS:
return True
return any(r.permissions.administrator or r.permissions.manage_messages for r in member.roles)

def can_use_role_cmd(member):
if member.id in OWNERS:
return True
return any(r.id == ROLE_CMD_ALLOWED_ROLE_ID for r in member.roles)

def can_timeout(member):
if member.id in OWNERS:
return True
if can_moderate(member):
return True
return TIMEOUT_ROLE_ID != 0 and any(r.id == TIMEOUT_ROLE_ID for r in member.roles)

def is_whitelisted(member):
if not member:
return False
if getattr(member, "id", None) in OWNERS:
return True
if getattr(member, "id", None) in SECURITY_WHITELIST_USERS:
return True
if hasattr(member, "roles"):
if any(r.id in SECURITY_WHITELIST_ROLES for r in member.roles):
return True
guild = getattr(member, "guild", None)
if guild:
bot_member = guild.me
if bot_member and member.top_role >= bot_member.top_role:
return True
return False

def is_new_account(user, days=7):
return (datetime.utcnow() - user.created_at.replace(tzinfo=None)) < timedelta(days=days)

def get_color(color: str) -> discord.Color:
colors = {
"white": discord.Color.from_rgb(255, 255, 255),
"red": discord.Color.from_rgb(220, 50, 50),
"green": discord.Color.from_rgb(50, 200, 50),
"blue": discord.Color.from_rgb(50, 100, 220),
"black": discord.Color.from_rgb(0, 0, 0),
}
return colors.get(color.lower(), discord.Color.from_rgb(0, 0, 0))

def eval_math_expression(expr: str):
import ast
expr = expr.strip()
import re as _re
if not _re.fullmatch(r"[\d\s\+\-\*\/\(\)\.]+", expr):
return None
try:
tree = ast.parse(expr, mode="eval")
allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num,
ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
ast.Mod, ast.Pow, ast.UAdd, ast.USub, ast.Constant)
for node in ast.walk(tree):
if not isinstance(node, allowed):
return None
result = eval(compile(tree, "<math>", "eval"), {"__builtins__": {}}, {})
if isinstance(result, (int, float)) and not isinstance(result, bool):
return int(result) if result == int(result) else None
except Exception:
return None
return None

async def get_latest_audit(guild, action, target_id=None):
try:
async for entry in guild.audit_logs(limit=5, action=action):
if target_id is None or (entry.target and entry.target.id == target_id):
return entry
except Exception:
pass
return None

# ── Compact log helper ───────────────────────────────────────────
# All logs are short, grey, one-line embeds. No noise.

_LOG_COLOR = discord.Color.from_rgb(100, 100, 100) # neutral grey

async def mod_log(guild, action: str, line: str):
"""Send ONE compact grey embed to the log channel."""
channel = guild.get_channel(LOG_CHANNEL_ID)
if not channel:
return
embed = discord.Embed(
description=f"**{action}** — {line}",
color=_LOG_COLOR,
timestamp=datetime.utcnow(),
)
embed.set_footer(text="7z Security")
try:
await channel.send(embed=embed)
except Exception:
pass

async def _reply_and_clean(ctx, text: str, delay: float = 4.0):
msg = await ctx.send(text)
await asyncio.sleep(delay)
for m in (ctx.message, msg):
try:
await m.delete()
except Exception:
pass

# ================================================================
# READY
# ================================================================

async def setup_hook():
bot.add_view(TicketButton())
bot.add_view(TicketCloseView())
bot.add_view(TicketDeleteView())

bot.setup_hook = setup_hook

@bot.event
async def on_ready():
print(f"Online: {bot.user}")
try:
await bot.tree.sync(guild=discord.Object(id=ALLOWED_GUILD_ID))
except Exception:
pass
asyncio.create_task(voice_keep_alive())
asyncio.create_task(cleanup_trackers())
for guild in bot.guilds:
current, last_user = _load_count(guild.id)
counting_state["current"] = current
counting_state["last_user"] = last_user if last_user else None
for guild in bot.guilds:
try:
invites = await guild.invites()
invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
except Exception:
pass

# ================================================================
# INVITE CACHE SYNC
# ================================================================

@bot.event
async def on_invite_create(invite: discord.Invite):
if invite.guild.id != ALLOWED_GUILD_ID:
return
invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses

@bot.event
async def on_invite_delete(invite: discord.Invite):
if invite.guild.id != ALLOWED_GUILD_ID:
return
invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

# ================================================================
# TRACKER CLEANUP
# ================================================================

async def cleanup_trackers():
await bot.wait_until_ready()
while True:
await asyncio.sleep(10)
now = datetime.utcnow()
for tracker, window in [
(timeout_tracker, 15),
(kick_tracker, 20),
(ban_tracker, 20),
(spam_tracker, SPAM_INTERVAL),
(mention_tracker, 10),
(invite_link_tracker, 30),
(channel_create_tracker, CREATE_INTERVAL),
(role_create_tracker, CREATE_INTERVAL),
]:
dead = [uid for uid, times in tracker.items()
if not [t for t in times if now - t < timedelta(seconds=window)]]
for uid in dead:
del tracker[uid]

# ================================================================
# VOICE KEEP-ALIVE
# ================================================================

async def voice_keep_alive():
await bot.wait_until_ready()
while VOICE_ALWAYS_ON:
try:
channel = bot.get_channel(CALL_VOICE_CHANNEL_ID)
if channel:
vc = discord.utils.get(bot.voice_clients, guild=channel.guild)
if vc is None:
try:
await channel.connect()
except Exception:
pass
elif not vc.is_connected():
try:
await vc.disconnect()
except Exception:
pass
try:
await channel.connect()
except Exception:
pass
except Exception:
pass
await asyncio.sleep(20)

# ================================================================
# WELCOME / AUTO-ROLE
# ================================================================

@bot.event
async def on_member_join(member: discord.Member):
if member.guild.id != ALLOWED_GUILD_ID:
return

# ── Hackban check ────────────────────────────────────────
# Check if this user is directly hackbanned
row = _hackban_get(member.guild.id, member.id)
if row:
try:
await member.ban(reason=f"Hackban — {row[1]}")
await mod_log(member.guild, "Hackban", f"{member} ({member.id}) versuchte beizutreten → gebannt (Hauptaccount).")
except Exception:
pass
return

# Check if this user is listed as an alt of any hackbanned user
for hb_uid, hb_reason, hb_alts in _hackban_all(member.guild.id):
alts_list = [a for a in hb_alts.split(",") if a]
if str(member.id) in alts_list:
try:
await member.ban(reason=f"Hackban Alt von {hb_uid} — {hb_reason}")
await mod_log(member.guild, "Hackban", f"{member} ({member.id}) ist Alt von {hb_uid} → gebannt.")
except Exception:
pass
return

# New account warning
if is_new_account(member, days=7):
await mod_log(member.guild, "New Account",
f"{member} ({member.id}) — Account jünger als 7 Tage (<t:{int(member.created_at.timestamp())}:R>).")

# Auto-role
role = member.guild.get_role(AUTO_ROLE_ID)
if role:
try:
await member.add_roles(role, reason="Auto Role")
except Exception:
pass

# Welcome message
welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
if welcome_channel:
try:
embed = discord.Embed(
description=(
f"Hey {member.mention},\n\n"
f"Wir freuen uns dich im **Corazon** Server begrüßen zu dürfen!\n"
f"Bitte beachte unser <#{RULES_CHANNEL_ID}>!\n\n"
f"• Sei nett\n"
f"• Viel Spaß!"
),
color=discord.Color.from_rgb(149, 165, 166),
)
await welcome_channel.send(embed=embed)
except Exception:
pass

# Invite tracking
try:
new_invites = await member.guild.invites()
old_cache = invite_cache.get(member.guild.id, {})
used_invite = next(
(inv for inv in new_invites if inv.uses > old_cache.get(inv.code, 0)),
None,
)
invite_cache[member.guild.id] = {inv.code: inv.uses for inv in new_invites}

invite_ch = member.guild.get_channel(INVITE_CHANNEL_ID)
if invite_ch:
if used_invite is None:
embed = discord.Embed(
title=member.guild.name,
description=f"{member.mention} ist beigetreten.\nEingeladen über **Vanity Link**",
color=discord.Color.from_rgb(149, 165, 166),
timestamp=datetime.utcnow(),
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.set_footer(text="Vanity Invite")
await invite_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
elif used_invite.inviter:
inviter = used_invite.inviter
_add_invite(member.guild.id, inviter.id, 1)
total, left, fake = _get_invites(member.guild.id, inviter.id)
real = total - left - fake
embed = discord.Embed(
title=member.guild.name,
description=(
f"{member.mention} ist beigetreten.\n"
f"Eingeladen von **{inviter.name}** und hat jetzt **{real} Invites**"
),
color=discord.Color.from_rgb(149, 165, 166),
timestamp=datetime.utcnow(),
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.set_footer(text=f"Invite code: {used_invite.code}")
await invite_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
else:
embed = discord.Embed(
title=member.guild.name,
description=f"{member.mention} ist beigetreten.\nEinladender konnte nicht ermittelt werden.",
color=discord.Color.from_rgb(149, 165, 166),
timestamp=datetime.utcnow(),
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.set_footer(text=f"Invite code: {used_invite.code}")
await invite_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
except Exception:
pass

@bot.event
async def on_member_remove(member: discord.Member):
guild = member.guild
if guild.id != ALLOWED_GUILD_ID:
return
try:
new_invites = await guild.invites()
invite_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}
except Exception:
pass

await asyncio.sleep(0.3)
entry = await get_latest_audit(guild, discord.AuditLogAction.kick, member.id)
if not entry or entry.target.id != member.id:
return
actor = entry.user
if not actor or is_whitelisted(guild.get_member(actor.id) or actor):
return

now = datetime.utcnow()
kick_tracker[actor.id] = [t for t in kick_tracker[actor.id] if now - t < timedelta(seconds=20)]
kick_tracker[actor.id].append(now)
if len(kick_tracker[actor.id]) >= 2:
try:
await guild.ban(actor, reason="Mass Kick (2+ in 20s)")
await mod_log(guild, "Auto-Ban", f"{actor} hat 2+ Mitglieder in 20s gekickt → gebannt.")
except Exception:
pass

# ================================================================
# BOOST / ROLE-TRIGGER
# ================================================================

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
if after.guild.id != ALLOWED_GUILD_ID:
return

if not before.premium_since and after.premium_since:
ch = after.guild.get_channel(BOOST_CHANNEL_ID)
if ch:
try:
await ch.send("thank you 🖤")
except Exception:
pass

before_ids = {r.id for r in before.roles}
after_ids = {r.id for r in after.roles}
if TRIGGER_ROLE_ID in after_ids and TRIGGER_ROLE_ID not in before_ids:
for extra_id in (EXTRA_ROLE_ID_1, EXTRA_ROLE_ID_2):
extra = after.guild.get_role(extra_id)
if extra:
try:
await after.add_roles(extra, reason="Trigger Role")
except Exception:
pass

# ================================================================
# MESSAGES
# ================================================================

@bot.event
async def on_message(message: discord.Message):
if message.author.bot:
await bot.process_commands(message)
return

guild = message.guild
if not guild or guild.id != ALLOWED_GUILD_ID:
await bot.process_commands(message)
return

if not is_whitelisted(message.author):
now = datetime.utcnow()

# spam
spam_tracker[message.author.id].append(now)
spam_tracker[message.author.id] = [
t for t in spam_tracker[message.author.id]
if now - t < timedelta(seconds=SPAM_INTERVAL)
]
if len(spam_tracker[message.author.id]) >= SPAM_MAX_MESSAGES:
try:
until = discord.utils.utcnow() + timedelta(seconds=SPAM_TIMEOUT_SECS)
await message.author.timeout(until, reason="Spam")
def is_spam(m): return m.author.id == message.author.id
try:
await message.channel.purge(limit=20, check=is_spam, bulk=True)
except Exception:
pass
await message.channel.send(f"{message.author.mention} getimeouted (Spam).", delete_after=5)
spam_tracker[message.author.id].clear()
await mod_log(guild, "Anti-Spam", f"{message.author} ({message.author.id}) → 5min Timeout (Spam).")
except Exception:
pass
return

# @everyone/@here spam
if "@everyone" in message.content or "@here" in message.content:
mention_tracker[message.author.id].append(now)
mention_tracker[message.author.id] = [
t for t in mention_tracker[message.author.id]
if now - t < timedelta(seconds=10)
]
if len(mention_tracker[message.author.id]) >= 2:
try:
await message.delete()
until = discord.utils.utcnow() + timedelta(seconds=SPAM_TIMEOUT_SECS)
await message.author.timeout(until, reason="Mass-Ping-Spam")
await message.channel.send(f"{message.author.mention} getimeouted (Mass-Ping).", delete_after=5)
mention_tracker[message.author.id].clear()
await mod_log(guild, "Mass-Ping", f"{message.author} ({message.author.id}) → Timeout.")
except Exception:
pass
return

# user/role mention spam
total_mentions = len(set(u.id for u in message.mentions)) + len(message.role_mentions)
if total_mentions >= MENTION_MAX:
try:
await message.delete()
until = discord.utils.utcnow() + timedelta(seconds=SPAM_TIMEOUT_SECS)
await message.author.timeout(until, reason="Mention-Spam")
await message.channel.send(f"{message.author.mention} getimeouted (Mention-Spam).", delete_after=5)
await mod_log(guild, "Mention-Spam", f"{message.author} ({message.author.id}) → {total_mentions} Mentions → Timeout.")
except Exception:
pass
return

# invite filter
if INVITE_PATTERN.search(message.content):
try:
await message.delete()
except Exception:
pass
invite_link_tracker[message.author.id].append(now)
invite_link_tracker[message.author.id] = [
t for t in invite_link_tracker[message.author.id]
if now - t < timedelta(seconds=30)
]
count = len(invite_link_tracker[message.author.id])
if count >= 2:
try:
until = discord.utils.utcnow() + timedelta(seconds=SPAM_TIMEOUT_SECS)
await message.author.timeout(until, reason="Invite-Spam")
await message.channel.send(f"{message.author.mention} getimeouted (Invite-Spam).", delete_after=5)
invite_link_tracker[message.author.id].clear()
await mod_log(guild, "Invite-Spam", f"{message.author} ({message.author.id}) → Timeout.")
except Exception:
pass
else:
try:
await message.channel.send(f"{message.author.mention} Invites sind hier nicht erlaubt.", delete_after=5)
await mod_log(guild, "Invite geblockt", f"{message.author} ({message.author.id}) hat einen Invite gepostet.")
except Exception:
pass
return

# auto-react
if message.channel.id in AUTO_REACT_CHANNEL_IDS:
if message.channel.id == ACTIVITY_CHECK_CHANNEL_ID:
try:
await message.add_reaction("✅")
except Exception:
pass
else:
try:
await message.add_reaction("✔️")
except Exception:
pass

# counting
if COUNTING_CHANNEL_ID and message.channel.id == COUNTING_CHANNEL_ID:
await handle_counting(message)
return

# AFK remove
if message.author.id in afk_users:
afk_users.pop(message.author.id)
try:
await message.channel.send(
f"Welcome back {message.author.mention}, dein AFK wurde entfernt.",
delete_after=5,
allowed_mentions=discord.AllowedMentions(users=True),
)
except Exception:
pass

# AFK notify
for mentioned in message.mentions:
if mentioned.id in afk_users:
reason, afk_since = afk_users[mentioned.id]
ts = int(afk_since.timestamp())
try:
await message.channel.send(
f"**{mentioned.name}** ist AFK seit <t:{ts}:R> — {reason}",
delete_after=8,
)
except Exception:
pass

await bot.process_commands(message)

# ================================================================
# MESSAGE DELETE → snipe
# ================================================================

@bot.event
async def on_message_delete(message: discord.Message):
if not message.guild or message.guild.id != ALLOWED_GUILD_ID:
return

if COUNTING_CHANNEL_ID and message.channel.id == COUNTING_CHANNEL_ID:
if message.author.bot:
return
next_num = counting_state["current"] + 1
try:
notice = await message.channel.send(f"Nachricht gelöscht. Nächste Zahl: **{next_num}**.")
counting_state["delete_notice"] = notice
except Exception:
pass
return

if message.author.bot or not message.content:
return
snipe_cache[message.channel.id] = (message.author, message.content, message.created_at)

# ================================================================
# COUNTING
# ================================================================

async def handle_counting(message: discord.Message):
content = message.content.strip()
expected = counting_state["current"] + 1
value = int(content) if content.lstrip("-").isdigit() else eval_math_expression(content)
if value is None:
try:
await message.delete()
except Exception:
pass
return
if message.author.id == counting_state["last_user"]:
try:
await message.delete()
except Exception:
pass
try:
n = await message.channel.send(f"{message.author.mention} Du kannst nicht zweimal hintereinander zählen.")
await asyncio.sleep(2)
await n.delete()
except Exception:
pass
return
if value == expected:
counting_state["current"] = expected
counting_state["last_user"] = message.author.id
_save_count(message.guild.id, expected, message.author.id)
if counting_state["delete_notice"]:
try:
await counting_state["delete_notice"].delete()
except Exception:
pass
counting_state["delete_notice"] = None
try:
await message.add_reaction("✔️")
except Exception:
pass
else:
try:
n = await message.channel.send(f"Falsche Zahl. Nächste: **{expected}**.")
await asyncio.sleep(2)
await n.delete()
except Exception:
pass

# ================================================================
# FIRST REACTOR — Activity-Check
# ================================================================

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
if user.bot:
return
if not reaction.message.guild or reaction.message.guild.id != ALLOWED_GUILD_ID:
return
if reaction.message.channel.id != ACTIVITY_CHECK_CHANNEL_ID:
return
msg_id = reaction.message.id
if msg_id in first_react_announced:
return
first_react_announced.add(msg_id)
try:
await reaction.message.channel.send(
f"{user.mention} war erster 🥇",
allowed_mentions=discord.AllowedMentions(users=True),
)
except Exception:
pass

# ================================================================
# TICKET SYSTEM
# ================================================================

def is_ticket_channel(channel) -> bool:
return channel.category_id == TICKET_CATEGORY_ID and channel.name.startswith("ticket-")

def can_manage_ticket(member) -> bool:
if member.id in OWNERS:
return True
return any(r.id == SUPPORT_ROLE_ID or r.permissions.administrator for r in member.roles)


class TicketCloseView(View):
def __init__(self):
super().__init__(timeout=None)

@discord.ui.button(label="Close", style=discord.ButtonStyle.gray, custom_id="ticket_close_btn")
async def close_btn(self, interaction: discord.Interaction, button: Button):
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
await interaction.response.defer()
try:
support_role = interaction.guild.get_role(SUPPORT_ROLE_ID)
await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
if support_role:
await interaction.channel.set_permissions(support_role, read_messages=True, send_messages=True)
await interaction.channel.send("Ticket geschlossen. Nur Support-Staff kann noch schreiben.")
except Exception as e:
await interaction.followup.send(f"Fehler: {e}", ephemeral=True)

@discord.ui.button(label="Delete", style=discord.ButtonStyle.gray, custom_id="ticket_delete_btn")
async def delete_btn(self, interaction: discord.Interaction, button: Button):
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
await interaction.response.defer()
try:
await interaction.channel.send("Ticket wird gelöscht.")
except Exception:
pass
await asyncio.sleep(1)
try:
await interaction.channel.delete(reason=f"Gelöscht von {interaction.user}")
except Exception:
pass
class TicketDeleteView(View):
def __init__(self):
super().__init__(timeout=None)

@discord.ui.button(label="Delete", style=discord.ButtonStyle.gray, custom_id="ticket_delete_btn_v2")
async def delete_btn(self, interaction: discord.Interaction, button: Button):
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
await interaction.response.defer()
try:
await interaction.channel.send("Ticket wird gelöscht.")
except Exception:
pass
await asyncio.sleep(1)
try:
await interaction.channel.delete(reason=f"Gelöscht von {interaction.user}")
except Exception:
pass


class TicketButton(View):
def __init__(self):
super().__init__(timeout=None)

@discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.blurple, custom_id="ticket_create")
async def create_ticket(self, interaction: discord.Interaction, button: Button):
guild = interaction.guild
category = guild.get_channel(TICKET_CATEGORY_ID)
if category:
for ch in category.text_channels:
if ch.name.startswith("ticket-"):
ow = ch.overwrites_for(interaction.user)
if ow.read_messages:
return await interaction.response.send_message(
f"Du hast bereits ein offenes Ticket: {ch.mention}", ephemeral=True
)
ticket_num = _increment_ticket_counter(guild.id)
support_role = guild.get_role(SUPPORT_ROLE_ID)
overwrites = {
guild.default_role: discord.PermissionOverwrite(read_messages=False),
interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
}
if support_role:
overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
for r in guild.roles:
if r.permissions.administrator and r not in overwrites:
overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
try:
ticket_channel = await guild.create_text_channel(
name=f"ticket-{ticket_num}", category=category,
overwrites=overwrites, reason=f"Ticket von {interaction.user}",
)
embed = discord.Embed(
title="Support Ticket",
description="Beschreibe dein Problem so genau wie möglich.\nUnser Team hilft dir so schnell wie möglich.",
color=discord.Color.from_rgb(149, 165, 166),
timestamp=datetime.utcnow(),
)
embed.set_footer(text=f"Ticket #{ticket_num}")
pings = f"{support_role.mention} {interaction.user.mention}" if support_role else interaction.user.mention
await ticket_channel.send(content=pings, embed=embed, view=TicketCloseView())
await interaction.response.send_message(f"Ticket erstellt: {ticket_channel.mention}", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)


@bot.tree.command(name="ticketpanel", description="Ticket-Panel senden",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def ticketpanel(interaction: discord.Interaction):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
channel = interaction.guild.get_channel(TICKET_PANEL_CHANNEL_ID)
if not channel:
return await interaction.response.send_message("Channel nicht gefunden.", ephemeral=True)
embed = discord.Embed(
title="Support",
description="Klicke auf den Button um ein Ticket zu öffnen.\nBleib respektvoll — wir helfen so schnell wie möglich.",
color=discord.Color.from_rgb(149, 165, 166),
)
try:
await channel.send(embed=embed, view=TicketButton())
await interaction.response.send_message("Panel gesendet.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)


@bot.command()
async def close(ctx: commands.Context):
if ctx.guild.id != ALLOWED_GUILD_ID or not is_ticket_channel(ctx.channel):
return
if not can_manage_ticket(ctx.author):
return await ctx.send("Keine Berechtigung.")
try:
await ctx.message.delete()
except Exception:
pass
support_role = ctx.guild.get_role(SUPPORT_ROLE_ID)
try:
await ctx.channel.set_permissions(ctx.guild.default_role, read_messages=False, send_messages=False)
if support_role:
await ctx.channel.set_permissions(support_role, read_messages=True, send_messages=True)
await ctx.send("Ticket geschlossen. Nur Support-Staff kann noch schreiben.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command(name="delete")
async def delete_ticket(ctx: commands.Context):
if ctx.guild.id != ALLOWED_GUILD_ID or not is_ticket_channel(ctx.channel):
return
if not can_manage_ticket(ctx.author):
return await ctx.send("Keine Berechtigung.")
try:
await ctx.message.delete()
except Exception:
pass
try:
await ctx.send("Ticket wird gelöscht.")
except Exception:
pass
await asyncio.sleep(1)
try:
await ctx.channel.delete(reason=f"Gelöscht von {ctx.author}")
except Exception as e:
await ctx.send(f"Fehler: {e}")

# ================================================================
# MODERATION — compact output
# ================================================================

@bot.command()
async def kick(ctx: commands.Context, member: discord.Member = None, *, reason: str = "Kein Grund angegeben"):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?kick @user [Grund]`")
if member.id in OWNERS:
return await _reply_and_clean(ctx, "Owner können nicht gekickt werden.")
if member.top_role >= ctx.guild.me.top_role:
return await _reply_and_clean(ctx, "Diese Person hat eine höhere Rolle als ich.")
try:
await member.kick(reason=f"{ctx.author}: {reason}")
await ctx.send(f"**{ctx.author.name}** hat **{member.name}** gekickt. | {reason}")
await mod_log(ctx.guild, "Kick", f"{ctx.author} hat {member} ({member.id}) gekickt. Grund: {reason}")
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command()
async def ban(ctx: commands.Context, member: discord.Member = None, *, reason: str = "Kein Grund angegeben"):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?ban @user [Grund]`")
if member.id in OWNERS:
return await _reply_and_clean(ctx, "Owner können nicht gebannt werden.")
if member.top_role >= ctx.guild.me.top_role:
return await _reply_and_clean(ctx, "Diese Person hat eine höhere Rolle als ich.")
try:
await member.ban(reason=f"{ctx.author}: {reason}", delete_message_days=1)
await ctx.send(f"**{ctx.author.name}** hat **{member.name}** gebannt. | {reason}")
await mod_log(ctx.guild, "Ban", f"{ctx.author} hat {member} ({member.id}) gebannt. Grund: {reason}")
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command()
async def unban(ctx: commands.Context, user_id: str = None, *, reason: str = "Kein Grund angegeben"):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if user_id is None or not user_id.isdigit():
return await _reply_and_clean(ctx, "Benutzung: `?unban <UserID> [Grund]`")
try:
user = await bot.fetch_user(int(user_id))
await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
await ctx.send(f"**{user}** wurde entbannt.")
await mod_log(ctx.guild, "Unban", f"{ctx.author} hat {user} ({user.id}) entbannt.")
except discord.NotFound:
await ctx.send("Nutzer nicht gefunden oder nicht gebannt.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command(aliases=["to"])
async def timeout(
ctx: commands.Context,
member: discord.Member = None,
duration: str = None,
*,
reason: str = "Kein Grund angegeben",
):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_timeout(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None or duration is None:
return await _reply_and_clean(ctx, "Benutzung: `?timeout @user <Zeit> [Grund]` — Einheiten: `s` `m` `h` `d`")
units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
unit = duration[-1].lower()
if unit not in units or not duration[:-1].isdigit():
return await _reply_and_clean(ctx, "Ungültige Zeit. Beispiel: `10m`, `2h`, `1d`")
seconds = int(duration[:-1]) * units[unit]
if seconds > 2419200:
return await _reply_and_clean(ctx, "Maximal 28 Tage.")
try:
until = discord.utils.utcnow() + timedelta(seconds=seconds)
await member.timeout(until, reason=f"{ctx.author}: {reason}")
await ctx.send(f"**{member.name}** wurde für **{duration}** getimeouted. | {reason}")
await mod_log(ctx.guild, "Timeout", f"{ctx.author} hat {member} ({member.id}) für {duration} getimeouted.")
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command(name="rto")
async def remove_timeout(ctx: commands.Context, member: discord.Member = None):
"""Entfernt den Timeout eines Mitglieds."""
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_timeout(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?rto @user`")
try:
await member.timeout(None, reason=f"Timeout entfernt von {ctx.author}")
await ctx.send(f"Timeout von **{member.name}** wurde entfernt.")
await mod_log(ctx.guild, "Timeout entfernt", f"{ctx.author} hat den Timeout von {member} ({member.id}) entfernt.")
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen.")
except Exception as e:
await ctx.send(f"Fehler: {e}")

# ================================================================
# HACKBAN
# ================================================================

@bot.command()
async def hackban(ctx: commands.Context, member: discord.Member = None, *, reason: str = "Kein Grund angegeben"):
"""
Bannt den User und merkt sich seine ID.
Falls er mit einem anderen Account zurückkommt und als Alt erkannt wird → direkt gebannt.
Da Discord keine IP-Abfragen per Bot erlaubt, funktioniert dies über manuelle Alt-Verknüpfung
mit ?hackban_addalt <HauptID> <AltID> und automatische Wiedereintritt-Erkennung.
"""
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?hackban @user [Grund]`")
if member.id in OWNERS:
return await _reply_and_clean(ctx, "Owner können nicht gehackbannt werden.")
try:
await ctx.guild.ban(member, reason=f"Hackban von {ctx.author}: {reason}", delete_message_days=1)
_hackban_add(ctx.guild.id, member.id, ctx.author.id, reason)
await ctx.send(f"**{member.name}** wurde gehackbannt. | {reason}\nFalls er mit einem Alt zurückkommt: `?hackban_addalt {member.id} <AltID>`")
await mod_log(ctx.guild, "Hackban", f"{ctx.author} hat {member} ({member.id}) gehackbannt. Grund: {reason}")
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen.")
except Exception as e:
await ctx.send(f"Fehler: {e}")


@bot.command()
async def hackban_addalt(ctx: commands.Context, main_id: str = None, alt_id: str = None):
"""Verknüpft einen bekannten Alt-Account mit einem Hackban."""
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if not main_id or not alt_id or not main_id.isdigit() or not alt_id.isdigit():
return await _reply_and_clean(ctx, "Benutzung: `?hackban_addalt <HauptID> <AltID>`")
row = _hackban_get(ctx.guild.id, int(main_id))
if not row:
return await ctx.send("Kein Hackban für diese ID gefunden.")
_hackban_add_alt(ctx.guild.id, int(main_id), int(alt_id))
# Try to ban the alt if they're currently in the server
try:
alt_member = ctx.guild.get_member(int(alt_id))
if alt_member:
await ctx.guild.ban(alt_member, reason=f"Hackban Alt von {main_id}")
await ctx.send(f"Alt **{alt_member}** wurde direkt gebannt und zur Liste hinzugefügt.")
else:
# Ban by ID even if not in server
await ctx.guild.ban(discord.Object(id=int(alt_id)), reason=f"Hackban Alt von {main_id}")
await ctx.send(f"Alt `{alt_id}` wurde gebannt und zur Liste hinzugefügt.")
await mod_log(ctx.guild, "Hackban-Alt", f"{ctx.author} hat Alt {alt_id} zu Hackban {main_id} hinzugefügt → gebannt.")
except Exception as e:
await ctx.send(f"Alt zur Liste hinzugefügt. Bann fehlgeschlagen: {e}")


@bot.command()
async def unhackban(ctx: commands.Context, user_id: str = None):
"""Entfernt den Hackban und entbannt den User."""
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if not user_id or not user_id.isdigit():
return await _reply_and_clean(ctx, "Benutzung: `?unhackban <UserID>`")
uid = int(user_id)
removed = _hackban_remove(ctx.guild.id, uid)
try:
await ctx.guild.unban(discord.Object(id=uid), reason=f"Unhackban von {ctx.author}")
await ctx.send(f"Hackban für `{user_id}` aufgehoben und entbannt.")
await mod_log(ctx.guild, "Unhackban", f"{ctx.author} hat Hackban für {user_id} aufgehoben.")
except discord.NotFound:
if removed:
await ctx.send(f"Hackban-Eintrag entfernt. Nutzer war nicht mehr gebannt.")
else:
await ctx.send("Kein Hackban-Eintrag oder Bann gefunden.")
except Exception as e:
await ctx.send(f"Fehler: {e}")

# ================================================================
# WARN SYSTEM
# ================================================================

@bot.command()
async def warn(ctx: commands.Context, member: discord.Member = None, *, reason: str = "Kein Grund angegeben"):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?warn @user [Grund]`")
warn_id = _add_warn(ctx.guild.id, member.id, ctx.author.id, reason)
warns = _get_warns(ctx.guild.id, member.id)
await ctx.send(f"**{member.name}** verwarnt (#{warn_id}, gesamt: {len(warns)}). | {reason}")
await mod_log(ctx.guild, "Warn", f"{ctx.author} hat {member} ({member.id}) verwarnt. #{warn_id} | {reason}")
try:
dm_embed = discord.Embed(
title=f"Verwarnung auf {ctx.guild.name}",
description=f"**Grund:** {reason}\n**Warn #{warn_id}** — Gesamt: {len(warns)}",
color=discord.Color.yellow(),
timestamp=datetime.utcnow(),
)
await member.send(embed=dm_embed)
except Exception:
pass


@bot.command()
async def warns(ctx: commands.Context, member: discord.Member = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?warns @user`")
warn_list = _get_warns(ctx.guild.id, member.id)
embed = discord.Embed(
title=f"Verwarnungen — {member}",
color=discord.Color.yellow(),
timestamp=datetime.utcnow(),
)
embed.set_thumbnail(url=member.display_avatar.url)
if not warn_list:
embed.description = "Keine Verwarnungen."
else:
for w_id, mod_id, reason, ts in warn_list:
mod = ctx.guild.get_member(mod_id)
mod_str = str(mod) if mod else f"ID:{mod_id}"
embed.add_field(name=f"#{w_id} — {ts[:10]}", value=f"**Grund:** {reason}\n**Mod:** {mod_str}", inline=False)
await ctx.send(embed=embed)


@bot.command()
async def clearwarn(ctx: commands.Context, warn_id: str = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if warn_id is None or not warn_id.isdigit():
return await _reply_and_clean(ctx, "Benutzung: `?clearwarn <WarnID>`")
if _del_warn(int(warn_id), ctx.guild.id):
await ctx.send(f"Verwarnung **#{warn_id}** gelöscht.", delete_after=5)
else:
await ctx.send(f"Verwarnung **#{warn_id}** nicht gefunden.", delete_after=5)


@bot.command()
async def clearwarns(ctx: commands.Context, member: discord.Member = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None:
return await _reply_and_clean(ctx, "Benutzung: `?clearwarns @user`")
count = _clear_warns(ctx.guild.id, member.id)
await ctx.send(f"**{count}** Verwarnungen von {member.mention} entfernt.", delete_after=5)

# ================================================================
# USERINFO / SERVERINFO / AVATAR
# ================================================================

@bot.tree.command(name="userinfo", description="Nutzerinfo anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
member = member or interaction.user
roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
embed = discord.Embed(
title=str(member),
color=member.color if member.color.value else discord.Color.from_rgb(149, 165, 166),
timestamp=datetime.utcnow(),
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.add_field(name="ID", value=member.id, inline=True)
embed.add_field(name="Nickname", value=member.nick or "—", inline=True)
embed.add_field(name="Bot", value="Ja" if member.bot else "Nein", inline=True)
embed.add_field(name="Erstellt", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
embed.add_field(name="Beigetreten", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
embed.add_field(
name="Boost",
value=f"<t:{int(member.premium_since.timestamp())}:R>" if member.premium_since else "Nein",
inline=True,
)
embed.add_field(name=f"Rollen ({len(roles)})", value=" ".join(roles[:20]) or "—", inline=False)
embed.set_footer(text=f"Angefragt von {interaction.user}")
await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Serverinfo anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def serverinfo(interaction: discord.Interaction):
g = interaction.guild
embed = discord.Embed(title=g.name, color=discord.Color.from_rgb(149, 165, 166), timestamp=datetime.utcnow())
if g.icon:
embed.set_thumbnail(url=g.icon.url)
embed.add_field(name="ID", value=g.id, inline=True)
embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
embed.add_field(name="Erstellt", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
embed.add_field(name="Mitglieder", value=g.member_count, inline=True)
embed.add_field(name="Rollen", value=len(g.roles), inline=True)
embed.add_field(name="Channels", value=len(g.channels), inline=True)
embed.add_field(name="Boosts", value=g.premium_subscription_count, inline=True)
embed.add_field(name="Boost-Level", value=g.premium_tier, inline=True)
embed.add_field(name="Verifikation", value=str(g.verification_level), inline=True)
await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Avatar eines Nutzers anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
member = member or interaction.user
embed = discord.Embed(title=f"Avatar — {member}", color=discord.Color.from_rgb(149, 165, 166))
embed.set_image(url=member.display_avatar.url)
await interaction.response.send_message(embed=embed)

# ================================================================
# PURGE
# ================================================================

@bot.command()
async def purge(ctx: commands.Context, amount: str = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_moderate(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
try:
await ctx.message.delete()
except Exception:
pass
if amount is None:
return await ctx.send("Benutzung: `?purge all` oder `?purge <Anzahl>`", delete_after=3)
if amount.lower() == "all":
deleted = await ctx.channel.purge(limit=None)
else:
if not amount.isdigit() or int(amount) < 1:
return await ctx.send("Ungültige Anzahl.", delete_after=3)
if int(amount) > 1000:
return await ctx.send("Maximal 1000 Nachrichten.", delete_after=3)
deleted = await ctx.channel.purge(limit=int(amount))
note = await ctx.send(f"{len(deleted)} Nachrichten gelöscht.")
await asyncio.sleep(3)
try:
await note.delete()
except Exception:
pass

# ================================================================
# ROLE COMMAND (admin-role protection built-in)
# ================================================================

def _find_role(guild, query):
query = query.strip()
if query.isdigit():
r = guild.get_role(int(query))
return [r] if r else []
exact = [r for r in guild.roles if r.name.lower() == query.lower()]
if exact:
return exact
return [r for r in guild.roles if query.lower() in r.name.lower()]
@bot.command(name="role")
async def role_cmd(ctx: commands.Context, member: discord.Member = None, *, role_input: str = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not can_use_role_cmd(ctx.author):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if member is None or role_input is None:
return await _reply_and_clean(ctx, "Benutzung: `?role @user <Rollenname oder ID>`")

matches = _find_role(ctx.guild, role_input)
if not matches:
return await _reply_and_clean(ctx, f"Keine Rolle gefunden für **{role_input}**.")
if len(matches) > 1:
names = ", ".join(f"`{r.name}`" for r in matches[:8])
return await _reply_and_clean(ctx, f"Mehrere Rollen gefunden: {names} — bitte genauer angeben.", delay=6)

role = matches[0]
if role >= ctx.guild.me.top_role:
return await _reply_and_clean(ctx, "Diese Rolle ist höher oder gleich meiner höchsten Rolle.")

# ── Admin-Rolle-Schutz ─────────────────────────────────
if role.permissions.administrator and ctx.author.id not in OWNERS:
# Remove the role from the target if they somehow got it, then kick the actor
try:
await member.remove_roles(role, reason="Admin-Rolle-Vergabe verhindert")
except Exception:
pass
try:
await ctx.author.kick(reason="Versuch eine Administrator-Rolle zu vergeben")
except Exception:
pass
await mod_log(ctx.guild, "Admin-Rollen-Schutz",
f"{ctx.author} ({ctx.author.id}) versuchte Admin-Rolle **{role.name}** zu vergeben → gekickt.")
return

try:
if role in member.roles:
await member.remove_roles(role, reason=f"?role von {ctx.author}")
action, color = "entfernt", discord.Color.red()
else:
await member.add_roles(role, reason=f"?role von {ctx.author}")
action, color = "hinzugefügt", discord.Color.green()

embed = discord.Embed(color=color, timestamp=datetime.utcnow())
embed.description = f"Rolle **{role.name}** für {member.mention} {action}."
embed.add_field(name="Rolle", value=f"{role.mention} ({role.id})")
embed.add_field(name="User", value=f"{member} ({member.id})")
embed.set_footer(text=f"Von {ctx.author} · {ctx.author.id}")
await ctx.send(embed=embed)
except discord.Forbidden:
await ctx.send("Fehlende Berechtigungen für diese Rolle.")
except Exception as e:
await ctx.send(f"Fehler: {e}")

# ================================================================
# SETCOUNT
# ================================================================

@bot.command()
async def setcount(ctx: commands.Context, number: int = None):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
if not is_owner(ctx.author.id):
return await _reply_and_clean(ctx, "Keine Berechtigung.")
if number is None:
return await _reply_and_clean(ctx, "Benutzung: `?setcount <Zahl>`")
counting_state["current"] = number
counting_state["last_user"] = None
_save_count(ctx.guild.id, number, 0)
await ctx.send(f"Zähler auf **{number}** gesetzt.", delete_after=5)
try:
await ctx.message.delete()
except Exception:
pass

# ================================================================
# INVITE SLASH COMMANDS
# ================================================================

@bot.tree.command(name="invite", description="Invite-Anzahl eines Nutzers anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def invite_cmd(interaction: discord.Interaction, member: discord.Member):
total, left, fake = _get_invites(interaction.guild.id, member.id)
real = total - left - fake
embed = discord.Embed(color=discord.Color.from_rgb(149, 165, 166), timestamp=datetime.utcnow())
embed.set_author(name=f"{member.name}'s Invites", icon_url=member.display_avatar.url)
embed.description = f"**{member.mention}** hat **{real}** Invites"
embed.add_field(name="Gesamt", value=total, inline=True)
embed.add_field(name="Links", value=left, inline=True)
embed.add_field(name="Fake", value=fake, inline=True)
await interaction.response.send_message(embed=embed)


@bot.tree.command(name="invites_set", description="Invite-Anzahl manuell setzen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def invites_set_cmd(interaction: discord.Interaction, member: discord.Member, amount: int):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
_set_invites(interaction.guild.id, member.id, amount)
await interaction.response.send_message(f"Invites für {member.mention} auf **{amount}** gesetzt.", ephemeral=True)


@bot.tree.command(name="leaderboard", description="Invite-Leaderboard anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def leaderboard_cmd(interaction: discord.Interaction):
top = _get_top(interaction.guild.id)
if not top:
return await interaction.response.send_message("Noch keine Invite-Daten.", ephemeral=True)
embed = discord.Embed(title="Invite Leaderboard", color=discord.Color.from_rgb(149, 165, 166), timestamp=datetime.utcnow())
medals = {1: "🥇", 2: "🥈", 3: "🥉"}
for i, (user_id, total, left, fake) in enumerate(top, start=1):
user = bot.get_user(user_id)
name = user.name if user else f"Unknown ({user_id})"
real = total - left - fake
embed.add_field(
name=f"{medals.get(i, f'{i}.')} {name}",
value=f"**{real}** Invites ({total} gesamt · {left} links · {fake} fake)",
inline=False,
)
await interaction.response.send_message(embed=embed)

# ================================================================
# SECURITY SYSTEM
# ================================================================

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
if channel.guild.id != ALLOWED_GUILD_ID:
return
if getattr(channel, "category_id", None) == TICKET_CATEGORY_ID and channel.name.startswith("ticket-"):
return
saved = {
"name": channel.name, "type": channel.type, "category": channel.category,
"position": channel.position, "overwrites": channel.overwrites,
"topic": getattr(channel, "topic", None), "nsfw": getattr(channel, "nsfw", False),
"slowmode": getattr(channel, "slowmode_delay", 0),
}
await asyncio.sleep(0.3)
entry = await get_latest_audit(channel.guild, discord.AuditLogAction.channel_delete)
if not entry:
return
user = entry.user
if not user or is_whitelisted(channel.guild.get_member(user.id) or user):
return
try:
await channel.guild.ban(user, reason="Channel-Löschung — Auto-Schutz")
await mod_log(channel.guild, "Auto-Ban", f"{user} ({user.id}) hat #{saved['name']} gelöscht → gebannt + Channel wiederhergestellt.")
except Exception:
pass
try:
kwargs = dict(name=saved["name"], overwrites=saved["overwrites"],
category=saved["category"], position=saved["position"], reason="Auto-Wiederherstellung")
if saved["type"] == discord.ChannelType.text:
if saved["topic"]:
kwargs["topic"] = saved["topic"]
kwargs["nsfw"] = saved["nsfw"]
kwargs["slowmode_delay"] = saved["slowmode"]
await channel.guild.create_text_channel(**kwargs)
elif saved["type"] == discord.ChannelType.voice:
await channel.guild.create_voice_channel(**kwargs)
else:
await channel.guild.create_text_channel(**kwargs)
except Exception:
pass


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
if channel.guild.id != ALLOWED_GUILD_ID:
return
await asyncio.sleep(0.3)
entry = await get_latest_audit(channel.guild, discord.AuditLogAction.channel_create)
if not entry:
return
user = entry.user
if not user or is_whitelisted(channel.guild.get_member(user.id) or user):
return
now = datetime.utcnow()
channel_create_tracker[user.id].append(now)
channel_create_tracker[user.id] = [t for t in channel_create_tracker[user.id] if now - t < timedelta(seconds=CREATE_INTERVAL)]
if len(channel_create_tracker[user.id]) >= CREATE_MAX:
try:
await channel.guild.ban(user, reason=f"Channel-Spam ({CREATE_MAX}+ in {CREATE_INTERVAL}s)")
await mod_log(channel.guild, "Auto-Ban", f"{user} ({user.id}) hat {len(channel_create_tracker[user.id])} Channels in {CREATE_INTERVAL}s erstellt → gebannt.")
channel_create_tracker[user.id].clear()
except Exception:
pass


@bot.event
async def on_guild_role_delete(role: discord.Role):
if role.guild.id != ALLOWED_GUILD_ID:
return
saved = {"name": role.name, "color": role.color, "permissions": role.permissions,
"hoist": role.hoist, "mentionable": role.mentionable}
await asyncio.sleep(0.3)
entry = await get_latest_audit(role.guild, discord.AuditLogAction.role_delete)
if not entry:
return
user = entry.user
if not user or is_whitelisted(role.guild.get_member(user.id) or user):
return
try:
await role.guild.ban(user, reason="Rollen-Löschung — Auto-Schutz")
await mod_log(role.guild, "Auto-Ban", f"{user} ({user.id}) hat Rolle **{saved['name']}** gelöscht → gebannt + Rolle wiederhergestellt.")
except Exception:
pass
try:
await role.guild.create_role(
name=saved["name"], color=saved["color"], permissions=saved["permissions"],
hoist=saved["hoist"], mentionable=saved["mentionable"], reason="Auto-Wiederherstellung",
)
except Exception:
pass


@bot.event
async def on_guild_role_create(role: discord.Role):
if role.guild.id != ALLOWED_GUILD_ID:
return
await asyncio.sleep(0.3)
entry = await get_latest_audit(role.guild, discord.AuditLogAction.role_create)
if not entry:
return
user = entry.user
if not user or is_whitelisted(role.guild.get_member(user.id) or user):
return
now = datetime.utcnow()
role_create_tracker[user.id].append(now)
role_create_tracker[user.id] = [t for t in role_create_tracker[user.id] if now - t < timedelta(seconds=CREATE_INTERVAL)]
if len(role_create_tracker[user.id]) >= CREATE_MAX:
try:
await role.guild.ban(user, reason=f"Rollen-Spam ({CREATE_MAX}+ in {CREATE_INTERVAL}s)")
await mod_log(role.guild, "Auto-Ban", f"{user} ({user.id}) hat {len(role_create_tracker[user.id])} Rollen in {CREATE_INTERVAL}s erstellt → gebannt.")
role_create_tracker[user.id].clear()
except Exception:
pass


@bot.event
async def on_webhooks_update(channel: discord.TextChannel):
if channel.guild.id != ALLOWED_GUILD_ID:
return
await asyncio.sleep(0.3)
entry = await get_latest_audit(channel.guild, discord.AuditLogAction.webhook_create)
if not entry:
return
user = entry.user
if not user or is_whitelisted(channel.guild.get_member(user.id) or user):
return
try:
for w in await channel.webhooks():
await w.delete()
await channel.guild.ban(user, reason="Webhook-Angriff — Auto-Schutz")
await mod_log(channel.guild, "Auto-Ban", f"{user} ({user.id}) hat einen Webhook erstellt → gebannt.")
except Exception:
pass


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
if guild.id != ALLOWED_GUILD_ID:
return
await asyncio.sleep(0.3)
entry = await get_latest_audit(guild, discord.AuditLogAction.ban)
if not entry:
return
actor = entry.user
if not actor or is_whitelisted(guild.get_member(actor.id) or actor):
return
now = datetime.utcnow()
ban_tracker[actor.id] = [t for t in ban_tracker[actor.id] if now - t < timedelta(seconds=20)]
ban_tracker[actor.id].append(now)
if len(ban_tracker[actor.id]) >= 2:
try:
await guild.ban(actor, reason="Mass-Ban (2+ in 20s)")
await mod_log(guild, "Auto-Ban", f"{actor} ({actor.id}) hat 2+ Mitglieder in 20s gebannt → gebannt.")
except Exception:
pass


@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
if entry.guild.id != ALLOWED_GUILD_ID:
return

# mass timeout
if entry.action == discord.AuditLogAction.member_update:
actor = entry.user
if not actor or is_whitelisted(entry.guild.get_member(actor.id) or actor):
return
changes = entry.changes
after_changes = {c.key: c.new for c in changes.after} if hasattr(changes, "after") else {}
if "timed_out_until" in after_changes and after_changes["timed_out_until"] is not None:
now = datetime.utcnow()
timeout_tracker[actor.id] = [t for t in timeout_tracker[actor.id] if now - t < timedelta(seconds=15)]
timeout_tracker[actor.id].append(now)
if len(timeout_tracker[actor.id]) >= 2:
try:
await entry.guild.ban(actor, reason="Mass-Timeout (2+ in 15s)")
await mod_log(entry.guild, "Auto-Ban", f"{actor} ({actor.id}) hat 2+ Mitglieder in 15s getimeouted → gebannt.")
except Exception:
pass

# admin perm grant via role_update
if entry.action == discord.AuditLogAction.role_update:
actor = entry.user
if not actor or is_whitelisted(entry.guild.get_member(actor.id) or actor):
return
role = entry.target
if not role or role.position >= entry.guild.me.top_role.position:
return
after_perms = None
if hasattr(entry.changes, "after"):
for c in entry.changes.after:
if c.key == "permissions":
after_perms = c.new
break
if after_perms and after_perms.administrator:
# Remove admin perm from the role
try:
p = discord.Permissions(after_perms.value)
p.administrator = False
await role.edit(permissions=p, reason="Admin-Perm verhindert")
except Exception:
pass
# Kick the person who tried
m = entry.guild.get_member(actor.id)
if m:
try:
await m.kick(reason="Versuch Admin-Perm zu vergeben")
await mod_log(entry.guild, "Admin-Perm-Schutz",
f"{actor} ({actor.id}) hat versucht Rolle **{role.name}** Admin-Rechte zu geben → Perm entfernt + gekickt.")
except Exception:
pass

# server update
if entry.action == discord.AuditLogAction.guild_update:
actor = entry.user
if actor and not actor.bot:
await mod_log(entry.guild, "Server geändert", f"{actor} ({actor.id}) hat Server-Einstellungen geändert.")

# bot added
if entry.action == discord.AuditLogAction.bot_add:
actor = entry.user
bot_added = entry.target
await mod_log(entry.guild, "Bot hinzugefügt",
f"{actor} ({actor.id}) hat Bot {bot_added} ({getattr(bot_added,'id','?')}) hinzugefügt.")

# ================================================================
# WHITELIST COMMANDS
# ================================================================

@bot.tree.command(name="whitelist_add", description="Nutzer zur Sicherheits-Whitelist hinzufügen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def whitelist_add(interaction: discord.Interaction, member: discord.Member):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
SECURITY_WHITELIST_USERS.add(member.id)
await interaction.response.send_message(f"{member.mention} zur Whitelist hinzugefügt.", ephemeral=True)


@bot.tree.command(name="whitelist_remove", description="Nutzer von der Sicherheits-Whitelist entfernen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def whitelist_remove(interaction: discord.Interaction, member: discord.Member):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
SECURITY_WHITELIST_USERS.discard(member.id)
await interaction.response.send_message(f"{member.mention} von Whitelist entfernt.", ephemeral=True)


@bot.tree.command(name="whitelist_list", description="Alle gewhitelisteten Nutzer anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def whitelist_list(interaction: discord.Interaction):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
if not SECURITY_WHITELIST_USERS:
return await interaction.response.send_message("Whitelist ist leer.", ephemeral=True)
names = []
for uid in SECURITY_WHITELIST_USERS:
u = bot.get_user(uid)
names.append(f"{u} (`{uid}`)" if u else f"Unknown (`{uid}`)")
await interaction.response.send_message("**Whitelist:**\n" + "\n".join(names), ephemeral=True)

# ================================================================
# /SEND (with optional embed, color, image)
# ================================================================

@bot.tree.command(name="send", description="Nachricht als Bot senden",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def send_cmd(
interaction: discord.Interaction,
channel: discord.TextChannel,
message: str,
embed: bool = True,
color: str = "black",
image: bool = False,
image_url: str = None,
):
"""
channel — Ziel-Channel
message — Nachrichtentext
embed — Als Embed senden (Standard: ja)
color — Embed-Farbe: black, white, red, green, blue
image — Bild anhängen (Standard: nein)
image_url — URL des Bildes (wird direkt angezeigt wie eine normale Bild-Nachricht)
"""
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)

if image and not image_url:
return await interaction.response.send_message("Bitte eine `image_url` angeben wenn `image` aktiviert ist.", ephemeral=True)

try:
if embed:
emb = discord.Embed(description=message, color=get_color(color))
if image and image_url:
emb.set_image(url=image_url)
await channel.send(embed=emb)
else:
# Plain text — if image, just send the URL directly so Discord renders it inline
content = message
if image and image_url:
content = f"{message}\n{image_url}" if message else image_url
await channel.send(content)
await interaction.response.send_message("Gesendet.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

# ================================================================
# HELP COMMAND
# ================================================================

@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
if ctx.guild.id != ALLOWED_GUILD_ID:
return
embed = discord.Embed(title="Command-Übersicht", color=discord.Color.from_rgb(149, 165, 166), timestamp=datetime.utcnow())
embed.add_field(name="Moderation", value=(
"`?kick @user [Grund]`\n"
"`?ban @user [Grund]`\n"
"`?unban <ID> [Grund]`\n"
"`?timeout @user <Zeit> [Grund]` — s m h d\n"
"`?rto @user` — Timeout entfernen\n"
"`?purge <Anzahl|all>`"
), inline=False)
embed.add_field(name="Hackban", value=(
"`?hackban @user [Grund]` — Bannt + merkt ID\n"
"`?hackban_addalt <HauptID> <AltID>` — Alt verknüpfen\n"
"`?unhackban <UserID>` — Hackban aufheben"
), inline=False)
embed.add_field(name="Verwarnungen", value=(
"`?warn @user [Grund]`\n"
"`?warns @user`\n"
"`?clearwarn <ID>`\n"
"`?clearwarns @user`"
), inline=False)
embed.add_field(name="Rollen", value="`?role @user <Name oder ID>`", inline=False)
embed.add_field(name="Info", value=(
"`/userinfo [@user]`\n"
"`/serverinfo`\n"
"`/avatar [@user]`"
), inline=False)
embed.add_field(name="Invites", value=(
"`/invite @user`\n"
"`/leaderboard`\n"
"`/invites_set @user <Anzahl>`"
), inline=False)
embed.add_field(name="Utility", value=(
"`/say #channel <Text>`\n"
"`/alts [Tage]` — Neue Accounts\n"
"`/snipe` — Letzte gelöschte Nachricht\n"
"`/afk [Grund]` — AFK setzen"
), inline=False)
embed.add_field(name="Tickets", value=(
"`?close` — Ticket schließen\n"
"`?delete` — Ticket löschen\n"
"`/adduser @user`\n"
"`/removeuser @user`\n"
"`/renameticket <Name>`"
), inline=False)
embed.add_field(name="Owner only", value=(
"`?setcount <Zahl>`\n"
"`?call`\n"
"`/send`\n"
"`/ticketpanel`\n"
"`/whitelist_add|remove|list`"
), inline=False)
embed.set_footer(text=f"Angefragt von {ctx.author}")
await ctx.send(embed=embed)

# ================================================================
# !CALL
# ================================================================

@bot.command()
async def call(ctx: commands.Context):
if ctx.guild.id != ALLOWED_GUILD_ID or not is_owner(ctx.author.id):
return
channel = bot.get_channel(CALL_VOICE_CHANNEL_ID)
try:
vc = ctx.voice_client
if vc and vc.is_connected():
await vc.move_to(channel)
else:
await channel.connect()
await ctx.send("Verbunden.", delete_after=3)
except Exception as e:
await ctx.send(f"Fehler: {e}")

# ================================================================
# SAY / ALTS / TICKET TOOLS
# ================================================================

@bot.tree.command(name="say", description="Nachricht als Bot senden (plain text)",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def say_cmd(interaction: discord.Interaction, channel: discord.TextChannel, text: str):
if interaction.user.id not in OWNERS:
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
try:
await channel.send(text)
await interaction.response.send_message("Gesendet.", ephemeral=True)
except discord.Forbidden:
await interaction.response.send_message("Fehlende Berechtigungen für diesen Channel.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)


@bot.tree.command(name="alts", description="Accounts jünger als X Tage anzeigen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def alts_cmd(interaction: discord.Interaction, days: int = 7):
if not can_moderate(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
if days < 1 or days > 365:
return await interaction.response.send_message("Tage müssen zwischen 1 und 365 liegen.", ephemeral=True)
now = datetime.utcnow()
alts = [m for m in interaction.guild.members
if not m.bot and (now - m.created_at.replace(tzinfo=None)) < timedelta(days=days)]
if not alts:
return await interaction.response.send_message(f"Keine Accounts jünger als {days} Tage.", ephemeral=True)
embed = discord.Embed(
title=f"Accounts jünger als {days} Tage",
color=discord.Color.orange(), timestamp=datetime.utcnow(),
)
embed.set_footer(text=f"{len(alts)} Account(s) gefunden")
lines = []
for m in sorted(alts, key=lambda x: x.created_at, reverse=True)[:20]:
age_days = (now - m.created_at.replace(tzinfo=None)).days
lines.append(f"{m.mention} — {age_days}d alt (<t:{int(m.created_at.timestamp())}:R>)")
embed.description = "\n".join(lines)
if len(alts) > 20:
embed.description += f"\n*...und {len(alts) - 20} weitere*"
await interaction.response.send_message(embed=embed)


@bot.tree.command(name="adduser", description="Nutzer zum aktuellen Ticket hinzufügen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def adduser_cmd(interaction: discord.Interaction, member: discord.Member):
if not is_ticket_channel(interaction.channel):
return await interaction.response.send_message("Kein Ticket-Channel.", ephemeral=True)
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
try:
await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
await interaction.response.send_message(f"{member.mention} zum Ticket hinzugefügt.")
except discord.Forbidden:
await interaction.response.send_message("Fehlende Berechtigungen.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)


@bot.tree.command(name="removeuser", description="Nutzer vom aktuellen Ticket entfernen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def removeuser_cmd(interaction: discord.Interaction, member: discord.Member):
if not is_ticket_channel(interaction.channel):
return await interaction.response.send_message("Kein Ticket-Channel.", ephemeral=True)
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
try:
await interaction.channel.set_permissions(member, overwrite=None)
await interaction.response.send_message(f"{member.mention} vom Ticket entfernt.")
except discord.Forbidden:
await interaction.response.send_message("Fehlende Berechtigungen.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)


@bot.tree.command(name="renameticket", description="Aktuelles Ticket umbenennen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def renameticket_cmd(interaction: discord.Interaction, name: str):
if not is_ticket_channel(interaction.channel):
return await interaction.response.send_message("Kein Ticket-Channel.", ephemeral=True)
if not can_manage_ticket(interaction.user):
return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
name = name.lower().replace(" ", "-")[:50]
try:
await interaction.channel.edit(name=f"ticket-{name}")
await interaction.response.send_message(f"Ticket umbenannt zu **ticket-{name}**.")
except discord.Forbidden:
await interaction.response.send_message("Fehlende Berechtigungen.", ephemeral=True)
except Exception as e:
await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

# ================================================================
# /SNIPE
# ================================================================

@bot.tree.command(name="snipe", description="Letzte gelöschte Nachricht in diesem Channel",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def snipe_cmd(interaction: discord.Interaction):
data = snipe_cache.get(interaction.channel.id)
if not data:
return await interaction.response.send_message("Nichts zu snipen.", ephemeral=True)
author, content, created_at = data
embed = discord.Embed(
description=content or "*(kein Text)*",
color=discord.Color.from_rgb(149, 165, 166),
timestamp=created_at,
)
embed.set_author(name=str(author), icon_url=author.display_avatar.url)
embed.set_footer(text=f"Gelöscht in #{interaction.channel.name}")
await interaction.response.send_message(embed=embed)

# ================================================================
# /AFK
# ================================================================

@bot.tree.command(name="afk", description="AFK-Status setzen",
guild=discord.Object(id=ALLOWED_GUILD_ID))
async def afk_cmd(interaction: discord.Interaction, reason: str = "AFK"):
reason = reason[:100]
afk_users[interaction.user.id] = (reason, datetime.utcnow())
await interaction.response.send_message(f"Du bist jetzt AFK: **{reason}**", ephemeral=True)

# ================================================================
# START
# ================================================================

bot.run(TOKEN)
