import sqlite3, discord, asyncio, os, re, pathlib
from discord.ext import commands
from discord.ui import View, Button
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.getenv("TOKEN")

ALLOWED_GUILD_ID         = 1512857571344515344
OWNERS: set[int]         = {1393725545853882509, 1325204584829947914}
CALL_VOICE_CHANNEL_ID    = 1516453482230583458
LOG_CHANNEL_ID           = 1516453487322595409
WELCOME_CHANNEL_ID       = 1516453500765208596
RULES_CHANNEL_ID         = 1516453504955187230
TICKET_PANEL_CHANNEL_ID  = 1516453513717219489
TICKET_CATEGORY_ID       = 1516453498269597697
SUPPORT_ROLE_ID          = 1516453478564630659
BOOST_CHANNEL_ID         = 1516453537024966708
AUTO_REACT_CHANNEL_IDS   = {1516453525041713225, 1516453552464203806}
ACTIVITY_CHECK_CHANNEL_ID= 1516453525041713225
COUNTING_CHANNEL_ID      = 1516453550996324392
AUTO_ROLE_ID             = 1516453463188307970
TRIGGER_ROLE_ID          = 1516453466921242875
EXTRA_ROLE_ID_1          = 1516453459115638876
EXTRA_ROLE_ID_2          = 1516453459115638876
INVITE_CHANNEL_ID        = 1516453511716405421
ROLE_CMD_ALLOWED_ROLE_ID = 1516453466921242875
TIMEOUT_ROLE_ID          = 1516453465810014460
VOICE_ALWAYS_ON          = True
SPAM_MAX=5; SPAM_INTERVAL=3; SPAM_TIMEOUT=300; MENTION_MAX=3; CREATE_MAX=3; CREATE_WIN=15
PING_MAX=2; PING_WINDOW=5
INVITE_RE = re.compile(r"(discord\.gg|discord\.com/invite)/\S+", re.IGNORECASE)
SECURITY_WHITELIST_USERS: set[int] = set()
SECURITY_WHITELIST_ROLES: set[int] = set()
SECURITY_MODULES = [
    "anti_spam","anti_mention","anti_invite","anti_webhook",
    "anti_channel_delete","anti_channel_create","anti_role_delete","anti_role_create",
    "anti_mass_ban","anti_mass_kick","anti_mass_timeout","anti_admin_perm","new_account_warn",
    "anti_mass_ping",
]

# ================================================================
#  SECURITY PUNISHMENTS — configurable per guild
#  Defaults (used if not set in DB)
# ================================================================
# Possible punishment values:
#   "none"        — do nothing (only delete/block)
#   "clear_roles" — remove all non-managed roles
#   "timeout"     — timeout for PUNISHMENT_TIMEOUT_DURATION seconds
#   "kick"        — kick the member
#   "ban"         — ban the member
SEC_PUNISHMENT_DEFAULTS = {
    "spam":        "timeout",      # ?timeout SPAM_TIMEOUT seconds
    "mass_ping":   "clear_roles",  # clear roles + block message
    "invite_spam": "timeout",
    "mass_ban":    "ban",
    "mass_kick":   "ban",
    "mass_timeout":"ban",
    "channel_del": "ban",
    "channel_spam":"ban",
    "role_del":    "ban",
    "role_spam":   "ban",
    "webhook":     "ban",
    "admin_perm":  "kick",
    "bot_add":     "ban",
}
SEC_PUNISHMENT_LABELS = {
    "spam":        "Spam (zu viele Nachrichten)",
    "mass_ping":   "Mass-Ping (@everyone/@here)",
    "invite_spam": "Invite-Link-Spam",
    "mass_ban":    "Mass-Ban",
    "mass_kick":   "Mass-Kick",
    "mass_timeout":"Mass-Timeout",
    "channel_del": "Channel-Löschung (unerlaubt)",
    "channel_spam":"Channel-Spam (zu viele erstellt)",
    "role_del":    "Rollen-Löschung (unerlaubt)",
    "role_spam":   "Rollen-Spam (zu viele erstellt)",
    "webhook":     "Webhook-Angriff",
    "admin_perm":  "Admin-Perm vergeben",
    "bot_add":     "Bot hinzugefügt (unerlaubt)",
}
VALID_PUNISHMENTS = ["none", "clear_roles", "timeout", "kick", "ban"]

BERLIN_TZ = ZoneInfo("Europe/Berlin")

NIGHT_MODE_ROLES = [
    1516514623413813488,
    1516453412106014851,
    1516457574151749724,
]
# role_id -> saved permissions dict: {perm_name: bool}
_night_saved_perms: dict[int, dict] = {}
# night mode enabled/disabled per guild (in-memory, persisted via DB)
_night_mode_enabled: dict[int, bool] = {}

intents = discord.Intents.default()
intents.members=intents.guilds=intents.message_content=True
intents.reactions=intents.voice_states=True
intents.guild_messages=intents.moderation=True
bot = commands.Bot(command_prefix=["!","?"],intents=intents,help_command=None,
    allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=False,replied_user=False))

timeout_tracker=defaultdict(list); kick_tracker=defaultdict(list); ban_tracker=defaultdict(list)
spam_tracker=defaultdict(list); mention_tracker=defaultdict(list); invite_link_tracker=defaultdict(list)
channel_create_tracker=defaultdict(list); role_create_tracker=defaultdict(list)
mass_ping_tracker=defaultdict(list)
counting_state:dict={"current":0,"last_user":None,"delete_notice":None}
first_react_announced:set=set(); invite_cache:dict={}; afk_users:dict={}

_DB="/data/bot.db" if pathlib.Path("/data").exists() else "bot.db"
if not pathlib.Path("/data").exists(): print("WARNING: /data not found — DB resets on restart.")
_db=sqlite3.connect(_DB,check_same_thread=False); _cur=_db.cursor()
_cur.executescript("""
CREATE TABLE IF NOT EXISTS invites(guild_id INT,user_id INT,invites INT DEFAULT 0,left_invites INT DEFAULT 0,fake_invites INT DEFAULT 0,PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INT NOT NULL,user_id INT NOT NULL,mod_id INT NOT NULL,reason TEXT NOT NULL,timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counting(guild_id INT PRIMARY KEY,current INT DEFAULT 0,last_user INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS ticket_counter(guild_id INT PRIMARY KEY,counter INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS hackbans(guild_id INT,user_id INT,mod_id INT,reason TEXT,alts TEXT DEFAULT '',PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS cfg_ids(guild_id INT,key TEXT,value INT,PRIMARY KEY(guild_id,key));
CREATE TABLE IF NOT EXISTS cfg_msgs(guild_id INT,key TEXT,value TEXT,PRIMARY KEY(guild_id,key));
CREATE TABLE IF NOT EXISTS sec_modules(guild_id INT,module TEXT,enabled INT DEFAULT 1,PRIMARY KEY(guild_id,module));
CREATE TABLE IF NOT EXISTS night_roles(guild_id INT,role_id INT,PRIMARY KEY(guild_id,role_id));
CREATE TABLE IF NOT EXISTS night_saved_perms(guild_id INT,role_id INT,perm_name TEXT,perm_value INT,PRIMARY KEY(guild_id,role_id,perm_name));
CREATE TABLE IF NOT EXISTS night_mode_state(guild_id INT PRIMARY KEY,enabled INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS sec_punishments(guild_id INT,event TEXT,punishment TEXT,PRIMARY KEY(guild_id,event));
"""); _db.commit()

_ID_DEF={
    "LOG_CHANNEL_ID":LOG_CHANNEL_ID,"WELCOME_CHANNEL_ID":WELCOME_CHANNEL_ID,"RULES_CHANNEL_ID":RULES_CHANNEL_ID,
    "TICKET_PANEL_CHANNEL_ID":TICKET_PANEL_CHANNEL_ID,"TICKET_CATEGORY_ID":TICKET_CATEGORY_ID,
    "SUPPORT_ROLE_ID":SUPPORT_ROLE_ID,"BOOST_CHANNEL_ID":BOOST_CHANNEL_ID,"COUNTING_CHANNEL_ID":COUNTING_CHANNEL_ID,
    "AUTO_ROLE_ID":AUTO_ROLE_ID,"TRIGGER_ROLE_ID":TRIGGER_ROLE_ID,"EXTRA_ROLE_ID_1":EXTRA_ROLE_ID_1,
    "EXTRA_ROLE_ID_2":EXTRA_ROLE_ID_2,"INVITE_CHANNEL_ID":INVITE_CHANNEL_ID,
    "ROLE_CMD_ALLOWED_ROLE_ID":ROLE_CMD_ALLOWED_ROLE_ID,"TIMEOUT_ROLE_ID":TIMEOUT_ROLE_ID,
    "CALL_VOICE_CHANNEL_ID":CALL_VOICE_CHANNEL_ID,
}
_MSG_DEF={
    "WELCOME_MSG":"Hey {mention},\n\nWelcome to **Corazon**!\nPlease read the rules: <#{rules}>\n\n- Be respectful\n- Have fun!",
    "BOOST_MSG":"thank you ",
    "TICKET_PANEL_DESC":"Click the button below to open a support ticket.",
    "TICKET_OPEN_MSG":"Please describe your issue and a staff member will assist you shortly.",
    "INVITE_VANITY_MSG":"**{member}** ist über den **Vanity-Link** beigetreten.",
    "INVITE_KNOWN_MSG":"**{member}** ist dem Server beigetreten.\nEingeladen von **{inviter}** — jetzt **{real} Einladungen**.",
    "INVITE_UNKNOWN_MSG":"**{member}** ist beigetreten. Einladender unbekannt.",
    "FIRST_REACT_MSG":"{mention} war der Erste! 🏆",
    "TICKET_CLOSE_MSG":"Dieses Ticket wurde geschlossen. Nur Staff-Mitglieder können diesen Kanal sehen.",
    "TICKET_DELETE_MSG":"Dieses Ticket wird in 3 Sekunden gelöscht.",
}
_MSG_LABELS={
    "WELCOME_MSG":"Willkommensnachricht",
    "BOOST_MSG":"Boost-Nachricht",
    "TICKET_PANEL_DESC":"Ticket-Panel Text",
    "TICKET_OPEN_MSG":"Ticket-Öffnungsnachricht",
    "INVITE_VANITY_MSG":"Invite-Tracking: Vanity-Link",
    "INVITE_KNOWN_MSG":"Invite-Tracking: Bekannter Einlader",
    "INVITE_UNKNOWN_MSG":"Invite-Tracking: Unbekannter Einlader",
    "FIRST_REACT_MSG":"Erste Reaktion (Activity-Check)",
    "TICKET_CLOSE_MSG":"Ticket geschlossen (Nachricht)",
    "TICKET_DELETE_MSG":"Ticket wird gelöscht (Nachricht)",
}

def _cid(g,k): _cur.execute("SELECT value FROM cfg_ids WHERE guild_id=? AND key=?",(g,k)); r=_cur.fetchone(); return r[0] if r else _ID_DEF.get(k,0)
def _sid(g,k,v):
    _cur.execute("INSERT INTO cfg_ids(guild_id,key,value)VALUES(?,?,?)ON CONFLICT(guild_id,key)DO UPDATE SET value=?",(g,k,v,v)); _db.commit()
    if k in globals(): globals()[k]=v
def _cmsg(g,k): _cur.execute("SELECT value FROM cfg_msgs WHERE guild_id=? AND key=?",(g,k)); r=_cur.fetchone(); return r[0] if r else _MSG_DEF.get(k,"")
def _smsg(g,k,v): _cur.execute("INSERT INTO cfg_msgs(guild_id,key,value)VALUES(?,?,?)ON CONFLICT(guild_id,key)DO UPDATE SET value=?",(g,k,v,v)); _db.commit()
def _sec(g,m): _cur.execute("SELECT enabled FROM sec_modules WHERE guild_id=? AND module=?",(g,m)); r=_cur.fetchone(); return r[0]==1 if r else True
def _ssec(g,m,e): _cur.execute("INSERT INTO sec_modules(guild_id,module,enabled)VALUES(?,?,?)ON CONFLICT(guild_id,module)DO UPDATE SET enabled=?",(g,m,int(e),int(e))); _db.commit()
def _inv_add(g,u,n=1): _cur.execute("INSERT INTO invites(guild_id,user_id,invites)VALUES(?,?,?)ON CONFLICT(guild_id,user_id)DO UPDATE SET invites=invites+?",(g,u,n,n)); _db.commit()
def _inv_get(g,u): _cur.execute("SELECT invites,left_invites,fake_invites FROM invites WHERE guild_id=? AND user_id=?",(g,u)); r=_cur.fetchone(); return r if r else (0,0,0)
def _inv_set(g,u,n): _cur.execute("INSERT INTO invites(guild_id,user_id,invites)VALUES(?,?,?)ON CONFLICT(guild_id,user_id)DO UPDATE SET invites=?",(g,u,n,n)); _db.commit()
def _inv_top(g,n=10): _cur.execute("SELECT user_id,invites,left_invites,fake_invites FROM invites WHERE guild_id=? ORDER BY invites DESC LIMIT ?",(g,n)); return _cur.fetchall()
def _warn_add(g,u,m,r): _cur.execute("INSERT INTO warns(guild_id,user_id,mod_id,reason,timestamp)VALUES(?,?,?,?,?)",(g,u,m,r,datetime.utcnow().isoformat())); _db.commit(); return _cur.lastrowid
def _warn_get(g,u): _cur.execute("SELECT id,mod_id,reason,timestamp FROM warns WHERE guild_id=? AND user_id=? ORDER BY id",(g,u)); return _cur.fetchall()
def _warn_del(w,g): _cur.execute("DELETE FROM warns WHERE id=? AND guild_id=?",(w,g)); _db.commit(); return _cur.rowcount>0
def _warn_clear(g,u): _cur.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?",(g,u)); _db.commit(); return _cur.rowcount
def _cnt_save(g,c,l): _cur.execute("INSERT INTO counting(guild_id,current,last_user)VALUES(?,?,?)ON CONFLICT(guild_id)DO UPDATE SET current=?,last_user=?",(g,c,l,c,l)); _db.commit()
def _cnt_load(g): _cur.execute("SELECT current,last_user FROM counting WHERE guild_id=?",(g,)); r=_cur.fetchone(); return (r[0],r[1]) if r else (0,0)
def _tkt_num(g): _cur.execute("INSERT INTO ticket_counter(guild_id,counter)VALUES(?,1)ON CONFLICT(guild_id)DO UPDATE SET counter=counter+1",(g,)); _db.commit(); _cur.execute("SELECT counter FROM ticket_counter WHERE guild_id=?",(g,)); return _cur.fetchone()[0]
def _tkt_cur(g): _cur.execute("SELECT counter FROM ticket_counter WHERE guild_id=?",(g,)); r=_cur.fetchone(); return r[0] if r else 0
def _hb_add(g,u,m,r,a=""): _cur.execute("INSERT INTO hackbans(guild_id,user_id,mod_id,reason,alts)VALUES(?,?,?,?,?)ON CONFLICT(guild_id,user_id)DO UPDATE SET mod_id=?,reason=?,alts=?",(g,u,m,r,a,m,r,a)); _db.commit()
def _hb_del(g,u): _cur.execute("DELETE FROM hackbans WHERE guild_id=? AND user_id=?",(g,u)); _db.commit(); return _cur.rowcount>0
def _hb_get(g,u): _cur.execute("SELECT mod_id,reason,alts FROM hackbans WHERE guild_id=? AND user_id=?",(g,u)); return _cur.fetchone()
def _hb_all(g): _cur.execute("SELECT user_id,reason,alts FROM hackbans WHERE guild_id=?",(g,)); return _cur.fetchall()
def _hb_alt(g,u,alt):
    r=_hb_get(g,u)
    if not r: return
    alts=set(r[2].split(",")) if r[2] else set(); alts.add(str(alt))
    _cur.execute("UPDATE hackbans SET alts=? WHERE guild_id=? AND user_id=?",(",".join(filter(None,alts)),g,u)); _db.commit()

def _night_roles_get(g):
    _cur.execute("SELECT role_id FROM night_roles WHERE guild_id=?",(g,)); return [r[0] for r in _cur.fetchall()]
def _night_role_add(g,rid):
    _cur.execute("INSERT OR IGNORE INTO night_roles(guild_id,role_id)VALUES(?,?)",(g,rid)); _db.commit()
def _night_role_del(g,rid):
    _cur.execute("DELETE FROM night_roles WHERE guild_id=? AND role_id=?",(g,rid)); _db.commit(); return _cur.rowcount>0

# ---- Night Mode: Perm persistence in DB ----
def _night_perms_save_db(g, rid, perms: dict):
    """Save a role's permissions to the DB (perm_name->bool)."""
    _cur.execute("DELETE FROM night_saved_perms WHERE guild_id=? AND role_id=?",(g,rid))
    for pname, pval in perms.items():
        _cur.execute("INSERT INTO night_saved_perms(guild_id,role_id,perm_name,perm_value)VALUES(?,?,?,?)",
                     (g, rid, pname, int(pval)))
    _db.commit()

def _night_perms_load_db(g, rid) -> dict | None:
    """Load saved permissions for a role from DB. Returns None if not found."""
    _cur.execute("SELECT perm_name,perm_value FROM night_saved_perms WHERE guild_id=? AND role_id=?",(g,rid))
    rows = _cur.fetchall()
    if not rows: return None
    return {pname: bool(pval) for pname, pval in rows}

def _night_perms_delete_db(g, rid):
    _cur.execute("DELETE FROM night_saved_perms WHERE guild_id=? AND role_id=?",(g,rid)); _db.commit()

# ---- Night Mode: enabled state ----
def _night_mode_get_enabled(g) -> bool:
    _cur.execute("SELECT enabled FROM night_mode_state WHERE guild_id=?",(g,))
    r=_cur.fetchone(); return r[0]==1 if r else True  # default: enabled

def _night_mode_set_enabled(g, val: bool):
    _cur.execute("INSERT INTO night_mode_state(guild_id,enabled)VALUES(?,?)ON CONFLICT(guild_id)DO UPDATE SET enabled=?",(g,int(val),int(val))); _db.commit()
    _night_mode_enabled[g] = val

# ---- Security punishments ----
def _sec_punishment_get(g, event: str) -> str:
    _cur.execute("SELECT punishment FROM sec_punishments WHERE guild_id=? AND event=?",(g,event))
    r=_cur.fetchone(); return r[0] if r else SEC_PUNISHMENT_DEFAULTS.get(event, "ban")

def _sec_punishment_set(g, event: str, punishment: str):
    _cur.execute("INSERT INTO sec_punishments(guild_id,event,punishment)VALUES(?,?,?)ON CONFLICT(guild_id,event)DO UPDATE SET punishment=?",(g,event,punishment,punishment)); _db.commit()

# ================================================================
#  HELPERS
# ================================================================

def is_owner(uid): return uid in OWNERS
def can_mod(m):
    if m.id in OWNERS: return True
    return any(r.permissions.administrator or r.permissions.manage_messages for r in m.roles)
def can_role(m):
    if m.id in OWNERS: return True
    aid=_cid(m.guild.id,"ROLE_CMD_ALLOWED_ROLE_ID"); return any(r.id==aid for r in m.roles)
def can_timeout(m):
    if m.id in OWNERS: return True
    if can_mod(m): return True
    tid=_cid(m.guild.id,"TIMEOUT_ROLE_ID"); return tid!=0 and any(r.id==tid for r in m.roles)
def whitelisted(m):
    if not m: return False
    if getattr(m,"id",None) in OWNERS: return True
    if getattr(m,"id",None) in SECURITY_WHITELIST_USERS: return True
    if hasattr(m,"roles"):
        if any(r.id in SECURITY_WHITELIST_ROLES for r in m.roles): return True
        g=getattr(m,"guild",None)
        if g and g.me and m.top_role>=g.me.top_role: return True
    return False
def new_account(u,days=7): return (datetime.utcnow()-u.created_at.replace(tzinfo=None))<timedelta(days=days)
def parse_color(c:str)->discord.Color:
    return {"white":discord.Color.from_rgb(255,255,255),"red":discord.Color.from_rgb(220,50,50),
            "green":discord.Color.from_rgb(50,200,50),"blue":discord.Color.from_rgb(50,100,220)
            }.get(c.lower(),discord.Color.from_rgb(0,0,0))
def parse_time(s:str):
    if not s or s[-1].lower() not in "smhd" or not s[:-1].isdigit(): return None
    return int(s[:-1])*{"s":1,"m":60,"h":3600,"d":86400}[s[-1].lower()]
def eval_math(expr:str):
    import ast as _a
    if not re.fullmatch(r"[\d\s\+\-\*\/\(\)\.]+",expr.strip()): return None
    try:
        t=_a.parse(expr.strip(),mode="eval")
        ok=(_a.Expression,_a.BinOp,_a.UnaryOp,_a.Num,_a.Add,_a.Sub,_a.Mult,_a.Div,_a.FloorDiv,_a.Mod,_a.Pow,_a.UAdd,_a.USub,_a.Constant)
        for n in _a.walk(t):
            if not isinstance(n,ok): return None
        r=eval(compile(t,"<m>","eval"),{"__builtins__":{}},{})
        return int(r) if isinstance(r,(int,float)) and r==int(r) else None
    except: return None
async def audit(guild,action,tid=None):
    try:
        async for e in guild.audit_logs(limit=5,action=action):
            if tid is None or (e.target and e.target.id==tid): return e
    except: pass
    return None
async def mlog(guild,action,line):
    ch=guild.get_channel(_cid(guild.id,"LOG_CHANNEL_ID"))
    if not ch: return
    try:
        await ch.send(embed=discord.Embed(description=f"**{action}** — {line}",
            color=0x2B2D31,timestamp=datetime.utcnow()).set_footer(text="Security Log"))
    except: pass
async def clean(ctx,text,delay=4.0):
    msg=await ctx.send(text); await asyncio.sleep(delay)
    for m in(ctx.message,msg):
        try: await m.delete()
        except: pass
def find_role(guild,q):
    q=q.strip()
    if q.isdigit():
        r=guild.get_role(int(q)); return [r] if r else []
    ex=[r for r in guild.roles if r.name.lower()==q.lower()]
    return ex or [r for r in guild.roles if q.lower() in r.name.lower()]
def bot_can_act(guild, member):
    return guild.me and member.top_role < guild.me.top_role

# ================================================================
#  SECURITY PUNISHMENT EXECUTOR
# ================================================================

async def _execute_punishment(guild: discord.Guild, member: discord.Member, event: str, reason: str):
    """Execute the configured punishment for a security event."""
    if not member or not bot_can_act(guild, member): return
    punishment = _sec_punishment_get(guild.id, event)

    if punishment == "none":
        return
    elif punishment == "clear_roles":
        removable = [r for r in member.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
        try:
            if removable:
                await member.remove_roles(*removable, reason=f"Security [{event}]: {reason}")
        except: pass
    elif punishment == "timeout":
        try:
            until = discord.utils.utcnow() + timedelta(seconds=SPAM_TIMEOUT)
            await member.timeout(until, reason=f"Security [{event}]: {reason}")
        except: pass
    elif punishment == "kick":
        try:
            await member.kick(reason=f"Security [{event}]: {reason}")
        except: pass
    elif punishment == "ban":
        try:
            await guild.ban(member, reason=f"Security [{event}]: {reason}", delete_message_days=1)
        except: pass

# ================================================================
#  NIGHT MODE — core functions
# ================================================================

def _perms_to_dict(perms: discord.Permissions) -> dict:
    """Convert discord.Permissions to a plain dict of {name: bool}."""
    return {name: value for name, value in perms}

def _dict_to_perms(d: dict) -> discord.Permissions:
    """Convert a plain dict back to discord.Permissions."""
    p = discord.Permissions.none()
    for name, value in d.items():
        try:
            setattr(p, name, bool(value))
        except: pass
    return p

async def do_night_off(guild: discord.Guild):
    """Activate Night Mode: remove permissions from configured roles, save them first."""
    role_ids = _night_roles_get(guild.id) or NIGHT_MODE_ROLES
    changed = []
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if not role: continue
        # Save current perms in memory AND DB
        perms_dict = _perms_to_dict(role.permissions)
        _night_saved_perms[role.id] = perms_dict
        _night_perms_save_db(guild.id, role.id, perms_dict)
        try:
            await role.edit(permissions=discord.Permissions.none(), reason="Night Mode aktiviert — 22:00 Berlin")
            changed.append(role.name)
        except: pass
    if changed:
        await mlog(guild, " Night Mode AKTIVIERT",
            f"Berechtigungen der folgenden Rollen wurden **entfernt**: {', '.join(f'`{n}`' for n in changed)}\n"
            f"Wiederherstellung automatisch um **09:00 Uhr Berlin**.")

async def do_night_on(guild: discord.Guild):
    """Deactivate Night Mode: restore saved permissions to configured roles."""
    role_ids = _night_roles_get(guild.id) or NIGHT_MODE_ROLES
    changed = []; no_save = []
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if not role: continue
        # Try memory first, then DB
        saved = _night_saved_perms.get(role.id) or _night_perms_load_db(guild.id, role.id)
        if saved:
            perms = _dict_to_perms(saved)
            try:
                await role.edit(permissions=perms, reason="Night Mode deaktiviert — 09:00 Berlin")
                changed.append(role.name)
                # Clean up saved perms
                _night_saved_perms.pop(role.id, None)
                _night_perms_delete_db(guild.id, role.id)
            except: pass
        else:
            no_save.append(role.name)
    lines = []
    if changed:
        lines.append(f"Berechtigungen der folgenden Rollen wurden **wiederhergestellt**: {', '.join(f'`{n}`' for n in changed)}")
    if no_save:
        lines.append(f"Keine gespeicherten Berechtigungen für: {', '.join(f'`{n}`' for n in no_save)}")
    if lines:
        await mlog(guild, " Night Mode DEAKTIVIERT", "\n".join(lines))

async def night_mode_loop():
    await bot.wait_until_ready()
    last_action: str | None = None
    while True:
        now_berlin = datetime.now(BERLIN_TZ)
        hour = now_berlin.hour
        action = None
        if hour == 22 and last_action != "off":
            action = "off"
        elif hour == 9 and last_action != "on":
            action = "on"
        if action:
            for guild in bot.guilds:
                if guild.id != ALLOWED_GUILD_ID: continue
                # Only run if night mode is enabled for this guild
                if not _night_mode_get_enabled(guild.id): continue
                if action == "off":
                    await do_night_off(guild)
                else:
                    await do_night_on(guild)
            last_action = action
        await asyncio.sleep(60)

# ================================================================
#  /nightmode — improved UI with status display
# ================================================================

class NightModeRoleSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, action: str):
        self.action = action
        options = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default(): continue
            options.append(discord.SelectOption(label=role.name[:100], value=str(role.id), description=f"ID: {role.id}"))
        options = options[:25]
        super().__init__(placeholder="Rolle auswählen...", options=options, min_values=1,
                         max_values=min(len(options), 10), custom_id="nm_role_sel")

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_roles = [int(v) for v in self.values]
        role_names = [interaction.guild.get_role(int(v)).name for v in self.values if interaction.guild.get_role(int(v))]
        await interaction.response.send_message(f"Ausgewählt: {', '.join(f'`{n}`' for n in role_names)}", ephemeral=True)


class NightModeManualView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild
        self.selected_roles: list[int] = []

    @discord.ui.button(label=" Night Mode AKTIVIEREN", style=discord.ButtonStyle.danger, row=0)
    async def turn_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in OWNERS:
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await do_night_off(self.guild)
        await interaction.followup.send(
            " **Night Mode wurde AKTIVIERT.**\n"
            "Die Berechtigungen der konfigurierten Rollen wurden **entfernt** und gespeichert.\n"
            "Sie werden automatisch um **09:00 Uhr (Berlin)** wiederhergestellt.", ephemeral=True)

    @discord.ui.button(label=" Night Mode DEAKTIVIEREN", style=discord.ButtonStyle.success, row=0)
    async def turn_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in OWNERS:
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await do_night_on(self.guild)
        await interaction.followup.send(
            " **Night Mode wurde DEAKTIVIERT.**\n"
            "Die gespeicherten Berechtigungen der konfigurierten Rollen wurden **wiederhergestellt**.", ephemeral=True)

    @discord.ui.button(label="⏸ Auto-NightMode pausieren", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_auto(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in OWNERS:
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        current = _night_mode_get_enabled(self.guild.id)
        new_val = not current
        _night_mode_set_enabled(self.guild.id, new_val)
        status = "**aktiviert** " if new_val else "**pausiert** ⏸"
        await interaction.response.send_message(
            f"Automatischer Night Mode wurde {status}.\n"
            f"{'Der Bot schaltet nun automatisch um 22:00 und 09:00 Uhr.' if new_val else 'Der Bot schaltet NICHT mehr automatisch — manuelle Steuerung möglich.'}", ephemeral=True)

    @discord.ui.button(label=" Rolle hinzufügen", style=discord.ButtonStyle.secondary, row=1)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in OWNERS:
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        view = discord.ui.View(timeout=60)
        sel = NightModeRoleSelect(interaction.guild, "add")
        view.add_item(sel)

        async def confirm_add(i2: discord.Interaction):
            added_info = []
            for rid in sel.values:
                role = i2.guild.get_role(int(rid))
                if not role: continue
                _night_role_add(i2.guild.id, int(rid))
                # Immediately save the current permissions of this role
                perms_dict = _perms_to_dict(role.permissions)
                _night_saved_perms[role.id] = perms_dict
                _night_perms_save_db(i2.guild.id, role.id, perms_dict)
                # Show which perms were saved
                active_perms = [p for p, v in perms_dict.items() if v]
                perm_preview = ", ".join(active_perms[:8]) + ("…" if len(active_perms) > 8 else "") if active_perms else "keine"
                added_info.append(f"**{role.name}** — gespeicherte Perms: `{perm_preview}`")
            msg = "\n".join(added_info) if added_info else "Keine Rollen hinzugefügt."
            await i2.response.edit_message(
                content=f" **Rollen zum Night Mode hinzugefügt:**\n{msg}\n\n"
                        f"Die aktuellen Berechtigungen wurden **sofort gespeichert**.\n"
                        f"Um 22:00 Uhr werden sie entfernt, um 09:00 Uhr wiederhergestellt.", view=None)

        btn = discord.ui.Button(label="Bestätigen", style=discord.ButtonStyle.success)
        btn.callback = confirm_add
        view.add_item(btn)
        await interaction.response.send_message("Rolle(n) für Night Mode auswählen:", view=view, ephemeral=True)

    @discord.ui.button(label=" Rolle entfernen", style=discord.ButtonStyle.secondary, row=1)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in OWNERS:
            return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        current_ids = _night_roles_get(interaction.guild.id) or NIGHT_MODE_ROLES
        options = []
        for rid in current_ids:
            role = interaction.guild.get_role(rid)
            if role:
                options.append(discord.SelectOption(label=role.name, value=str(rid)))
        if not options:
            return await interaction.response.send_message("Keine Night Mode Rollen konfiguriert.", ephemeral=True)

        view = discord.ui.View(timeout=60)
        sel = discord.ui.Select(placeholder="Rollen zum Entfernen...", options=options,
                                min_values=1, max_values=len(options))

        async def do_remove(i2: discord.Interaction):
            removed = []
            for rid in sel.values:
                if _night_role_del(i2.guild.id, int(rid)):
                    r = i2.guild.get_role(int(rid))
                    removed.append(r.name if r else rid)
                    _night_saved_perms.pop(int(rid), None)
                    _night_perms_delete_db(i2.guild.id, int(rid))
            await i2.response.edit_message(content=f" Entfernt: {', '.join(f'`{n}`' for n in removed)}", view=None)

        sel.callback = do_remove
        view.add_item(sel)
        await interaction.response.send_message("Rollen zum Entfernen auswählen:", view=view, ephemeral=True)

    @discord.ui.button(label=" Aktuelle Rollen anzeigen", style=discord.ButtonStyle.secondary, row=1)
    async def list_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_ids = _night_roles_get(interaction.guild.id) or NIGHT_MODE_ROLES
        auto_enabled = _night_mode_get_enabled(self.guild.id)
        lines = []
        for rid in current_ids:
            role = interaction.guild.get_role(rid)
            # Check if perms are saved (= night mode is currently ON for this role)
            has_saved = rid in _night_saved_perms or _night_perms_load_db(self.guild.id, rid) is not None
            status = " Perms entfernt (Night Mode aktiv)" if has_saved else " Perms aktiv (Normal)"
            lines.append(f"- {role.mention if role else f'Unbekannte Rolle (`{rid}`)'} — {status}")
        auto_status = " Aktiviert (22:00 OFF / 09:00 ON)" if auto_enabled else "⏸ Pausiert (manuell)"
        embed = discord.Embed(
            title=" Night Mode — Konfigurierte Rollen",
            description=(
                f"**Automatik:** {auto_status}\n\n" +
                ("\n".join(lines) if lines else "Keine Rollen konfiguriert.")
            ),
            color=0x2B2D31)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="nightmode", description="Night Mode verwalten — aktivieren, deaktivieren oder Rollen konfigurieren.",
    guild=discord.Object(id=ALLOWED_GUILD_ID))
async def nightmode_cmd(interaction: discord.Interaction):
    if interaction.user.id not in OWNERS:
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)

    guild = interaction.guild
    current_ids = _night_roles_get(guild.id) or NIGHT_MODE_ROLES
    auto_enabled = _night_mode_get_enabled(guild.id)

    role_lines = []
    for rid in current_ids:
        role = guild.get_role(rid)
        has_saved = rid in _night_saved_perms or _night_perms_load_db(guild.id, rid) is not None
        nm_status = " aktiv" if has_saved else " normal"
        role_lines.append(f"- {role.mention if role else f'`{rid}`'} ({nm_status})")

    auto_status = " Automatik AN" if auto_enabled else "⏸ Automatik PAUSIERT"

    embed = discord.Embed(
        title=" Night Mode",
        description=(
            f"**Status:** {auto_status}\n\n"
            "**Was macht Night Mode?**\n"
            "→ **22:00 Uhr Berlin:** Die Berechtigungen der konfigurierten Rollen werden **entfernt** "
            "(gespeichert, damit sie wiederhergestellt werden können).\n"
            "→ **09:00 Uhr Berlin:** Die Berechtigungen werden **wiederhergestellt** — exakt wie vorher.\n\n"
            "**Konfigurierte Rollen:**\n" +
            ("\n".join(role_lines) if role_lines else "Keine") +
            "\n\n**Buttons unten:**\n"
            " **Aktivieren** = Perms jetzt entfernen (Night Mode an)\n"
            " **Deaktivieren** = Perms jetzt wiederherstellen (Night Mode aus)\n"
            "⏸ **Pausieren** = Automatik ein-/ausschalten\n"
            " **Rolle hinzufügen** = Rolle mit aktuellen Perms zum Night Mode hinzufügen\n"
            "Die Perms der Rolle werden **sofort beim Hinzufügen gespeichert**."
        ),
        color=0x2B2D31)

    view = NightModeManualView(guild)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ================================================================
#  READY / BACKGROUND TASKS
# ================================================================

async def setup_hook():
    bot.add_view(TicketButton()); bot.add_view(TicketActionView()); bot.add_view(TicketDeleteView())
bot.setup_hook=setup_hook

async def tracker_cleanup():
    await bot.wait_until_ready()
    while True:
        await asyncio.sleep(10); now=datetime.utcnow()
        # Normal trackers (list of datetimes)
        for t,w in[(timeout_tracker,15),(kick_tracker,20),(ban_tracker,20),(spam_tracker,SPAM_INTERVAL),
                   (mention_tracker,10),(invite_link_tracker,30),
                   (channel_create_tracker,CREATE_WIN),(role_create_tracker,CREATE_WIN)]:
            dead=[uid for uid,ts in t.items() if not[x for x in ts if now-x<timedelta(seconds=w)]]
            for uid in dead: del t[uid]
        # mass_ping_tracker stores (datetime, message) tuples
        dead=[uid for uid,entries in mass_ping_tracker.items()
              if not[ts for ts,_ in entries if now-ts<timedelta(seconds=PING_WINDOW)]]
        for uid in dead: del mass_ping_tracker[uid]

async def voice_loop():
    await bot.wait_until_ready()
    while VOICE_ALWAYS_ON:
        try:
            ch=bot.get_channel(CALL_VOICE_CHANNEL_ID)
            if ch:
                vc=discord.utils.get(bot.voice_clients,guild=ch.guild)
                if vc is None:
                    try: await ch.connect()
                    except: pass
                elif not vc.is_connected():
                    try: await vc.disconnect()
                    except: pass
                    try: await ch.connect()
                    except: pass
        except: pass
        await asyncio.sleep(20)

@bot.event
async def on_invite_create(inv:discord.Invite):
    if inv.guild.id!=ALLOWED_GUILD_ID: return
    invite_cache.setdefault(inv.guild.id,{})[inv.code]=inv.uses
@bot.event
async def on_invite_delete(inv:discord.Invite):
    if inv.guild.id!=ALLOWED_GUILD_ID: return
    invite_cache.get(inv.guild.id,{}).pop(inv.code,None)

# ================================================================
#  MEMBER JOIN / LEAVE / UPDATE
# ================================================================

@bot.event
async def on_member_join(member:discord.Member):
    if member.guild.id!=ALLOWED_GUILD_ID: return
    row=_hb_get(member.guild.id,member.id)
    if row:
        try: await member.ban(reason=f"Hackban — {row[1]}")
        except: pass
        return
    for huid,_,halts in _hb_all(member.guild.id):
        if str(member.id) in [a for a in halts.split(",") if a]:
            try: await member.ban(reason=f"Hackban Alt of {huid}")
            except: pass
            return
    if _sec(member.guild.id,"new_account_warn") and new_account(member):
        await mlog(member.guild,"New Account",f"{member} ({member.id}) — Account ist weniger als 7 Tage alt.")
    ar=member.guild.get_role(_cid(member.guild.id,"AUTO_ROLE_ID"))
    if ar:
        try: await member.add_roles(ar,reason="Auto Role")
        except: pass

    wch=member.guild.get_channel(_cid(member.guild.id,"WELCOME_CHANNEL_ID"))
    if wch:
        try:
            raw=_cmsg(member.guild.id,"WELCOME_MSG")
            await wch.send(
                embed=discord.Embed(
                    description=raw.format(mention=member.mention,rules=_cid(member.guild.id,"RULES_CHANNEL_ID")),
                    color=0x2B2D31),
                allowed_mentions=discord.AllowedMentions(users=True))
        except: pass

    try:
        new_invs=await member.guild.invites(); old=invite_cache.get(member.guild.id,{})
        used=next((i for i in new_invs if i.uses>old.get(i.code,0)),None)
        invite_cache[member.guild.id]={i.code:i.uses for i in new_invs}
        ich=member.guild.get_channel(_cid(member.guild.id,"INVITE_CHANNEL_ID"))
        if ich:
            if used is None:
                emb=discord.Embed(title=member.guild.name,
                    description=_cmsg(member.guild.id,"INVITE_VANITY_MSG").format(member=str(member),mention=member.mention),
                    color=0x2B2D31,timestamp=datetime.utcnow())
            elif used.inviter:
                _inv_add(member.guild.id,used.inviter.id); total,left,fake=_inv_get(member.guild.id,used.inviter.id)
                real=total-left-fake
                emb=discord.Embed(title=member.guild.name,
                    description=_cmsg(member.guild.id,"INVITE_KNOWN_MSG").format(member=str(member),mention=member.mention,inviter=used.inviter.name,real=real),
                    color=0x2B2D31,timestamp=datetime.utcnow())
            else:
                emb=discord.Embed(title=member.guild.name,
                    description=_cmsg(member.guild.id,"INVITE_UNKNOWN_MSG").format(member=str(member),mention=member.mention),
                    color=0x2B2D31,timestamp=datetime.utcnow())
            emb.set_thumbnail(url=member.display_avatar.url)
            await ich.send(embed=emb,allowed_mentions=discord.AllowedMentions(users=True))
    except: pass

@bot.event
async def on_member_remove(member:discord.Member):
    if member.guild.id!=ALLOWED_GUILD_ID: return
    try: invite_cache[member.guild.id]={i.code:i.uses for i in await member.guild.invites()}
    except: pass
    if not _sec(member.guild.id,"anti_mass_kick"): return
    await asyncio.sleep(0.3)
    e=await audit(member.guild,discord.AuditLogAction.kick,member.id)
    if not e or e.target.id!=member.id: return
    actor=e.user
    if not actor or whitelisted(member.guild.get_member(actor.id) or actor): return
    now=datetime.utcnow()
    kick_tracker[actor.id]=[t for t in kick_tracker[actor.id] if now-t<timedelta(seconds=20)]
    kick_tracker[actor.id].append(now)
    if len(kick_tracker[actor.id])>=2:
        m = member.guild.get_member(actor.id)
        await _execute_punishment(member.guild, m or actor, "mass_kick", "Mass Kick (2+ in 20s)")
        await mlog(member.guild,"Auto-Strafe (Mass Kick)",f"{actor} ({actor.id}) hat 2+ Mitglieder in 20s gekickt.")

@bot.event
async def on_member_update(before:discord.Member,after:discord.Member):
    if after.guild.id!=ALLOWED_GUILD_ID: return
    if not before.premium_since and after.premium_since:
        ch=after.guild.get_channel(_cid(after.guild.id,"BOOST_CHANNEL_ID"))
        if ch:
            try:
                await asyncio.sleep(5)
                await ch.send(_cmsg(after.guild.id,"BOOST_MSG"))
            except: pass
    b={r.id for r in before.roles}; a={r.id for r in after.roles}
    tr=_cid(after.guild.id,"TRIGGER_ROLE_ID")
    if tr and tr in a and tr not in b:
        for k in("EXTRA_ROLE_ID_1","EXTRA_ROLE_ID_2"):
            eid=_cid(after.guild.id,k)
            if not eid: continue
            ex=after.guild.get_role(eid)
            if ex and ex not in after.roles:
                try: await after.add_roles(ex,reason="Trigger Role")
                except: pass

# ================================================================
#  MESSAGES
# ================================================================

@bot.event
async def on_message(message:discord.Message):
    if message.author.bot: await bot.process_commands(message); return
    g=message.guild
    if not g or g.id!=ALLOWED_GUILD_ID: await bot.process_commands(message); return

    if message.channel.id==ACTIVITY_CHECK_CHANNEL_ID:
        if "@everyone" in message.content or "@here" in message.content:
            try: await message.delete()
            except: pass
            return

    if not whitelisted(message.author):
        now=datetime.utcnow()
        if _sec(g.id,"anti_spam"):
            spam_tracker[message.author.id].append(now)
            spam_tracker[message.author.id]=[t for t in spam_tracker[message.author.id] if now-t<timedelta(seconds=SPAM_INTERVAL)]
            if len(spam_tracker[message.author.id])>=SPAM_MAX:
                m = g.get_member(message.author.id)
                if m:
                    await _execute_punishment(g, m, "spam", f"Spam ({SPAM_MAX}+ Nachrichten in {SPAM_INTERVAL}s)")
                try:
                    def chk(msg): return msg.author.id==message.author.id
                    await message.channel.purge(limit=20,check=chk,bulk=True)
                except: pass
                try: await message.channel.send(f"{message.author.mention} Du wurdest wegen Spam bestraft.",delete_after=5)
                except: pass
                spam_tracker[message.author.id].clear()
                await mlog(g,"Auto-Strafe (Spam)",f"{message.author} ({message.author.id}) — Spam erkannt.")
                return

        if _sec(g.id,"anti_mention"):
            # @everyone / @here is handled by _on_message_mass_ping listener (2-ping-in-5s rule)
            # Here we only handle excessive individual user/role mentions
            total=len(set(u.id for u in message.mentions))+len(message.role_mentions)
            if total>=MENTION_MAX and "@everyone" not in message.content and "@here" not in message.content:
                try: await message.delete()
                except: pass
                try: await message.channel.send(f"{message.author.mention} Zu viele Erwähnungen in einer Nachricht.",delete_after=5)
                except: pass
                return

        if _sec(g.id,"anti_invite") and INVITE_RE.search(message.content):
            try: await message.delete()
            except: pass
            invite_link_tracker[message.author.id].append(now)
            invite_link_tracker[message.author.id]=[t for t in invite_link_tracker[message.author.id] if now-t<timedelta(seconds=30)]
            if len(invite_link_tracker[message.author.id])>=2:
                m = g.get_member(message.author.id)
                if m:
                    await _execute_punishment(g, m, "invite_spam", "Invite-Link-Spam")
                try: await message.channel.send(f"{message.author.mention} Du wurdest wegen Invite-Link-Spam bestraft.",delete_after=5)
                except: pass
                invite_link_tracker[message.author.id].clear()
                await mlog(g,"Auto-Strafe (Invite Spam)",f"{message.author} ({message.author.id}) — Invite-Spam.")
            else:
                try: await message.channel.send(f"{message.author.mention} Invite-Links sind auf diesem Server nicht erlaubt.",delete_after=5)
                except: pass
            return

    if message.channel.id in AUTO_REACT_CHANNEL_IDS:
        emoji="✅" if message.channel.id==ACTIVITY_CHECK_CHANNEL_ID else "✔️"
        try: await message.add_reaction(emoji)
        except: pass

    if COUNTING_CHANNEL_ID and message.channel.id==COUNTING_CHANNEL_ID:
        await handle_counting(message); return

    if message.author.id in afk_users:
        afk_users.pop(message.author.id)
        try: await message.channel.send(f"Willkommen zurück {message.author.mention}, dein AFK-Status wurde entfernt.",delete_after=5,allowed_mentions=discord.AllowedMentions(users=True))
        except: pass

    for u in message.mentions:
        if u.id in afk_users:
            r,ts=afk_users[u.id]
            try: await message.channel.send(f"**{u.name}** ist AFK seit <t:{int(ts.timestamp())}:R> — {r}",delete_after=8)
            except: pass

    await bot.process_commands(message)

@bot.event
async def on_message_delete(message:discord.Message):
    if not message.guild or message.guild.id!=ALLOWED_GUILD_ID: return
    if COUNTING_CHANNEL_ID and message.channel.id==COUNTING_CHANNEL_ID:
        if message.author.bot: return
        try:
            n=await message.channel.send(f"Eine Nachricht wurde gelöscht. Nächste Zahl: **{counting_state['current']+1}**.")
            counting_state["delete_notice"]=n
        except: pass

async def handle_counting(message:discord.Message):
    c=message.content.strip(); expected=counting_state["current"]+1
    value=int(c) if c.lstrip("-").isdigit() else eval_math(c)
    if value is None:
        try: await message.delete()
        except: pass
        return
    if message.author.id==counting_state["last_user"]:
        try: await message.delete()
        except: pass
        try:
            n=await message.channel.send(f"{message.author.mention} Du kannst nicht zweimal hintereinander zählen.")
            await asyncio.sleep(2); await n.delete()
        except: pass
        return
    if value==expected:
        counting_state["current"]=expected; counting_state["last_user"]=message.author.id
        _cnt_save(message.guild.id,expected,message.author.id)
        if counting_state["delete_notice"]:
            try: await counting_state["delete_notice"].delete()
            except: pass
            counting_state["delete_notice"]=None
        try: await message.add_reaction("✔️")
        except: pass
    else:
        try:
            n=await message.channel.send(f"Falsch. Nächste Zahl: **{expected}**.")
            await asyncio.sleep(2); await n.delete()
        except: pass

@bot.event
async def on_reaction_add(reaction:discord.Reaction,user:discord.User):
    if user.bot: return
    if not reaction.message.guild or reaction.message.guild.id!=ALLOWED_GUILD_ID: return
    if reaction.message.channel.id!=ACTIVITY_CHECK_CHANNEL_ID: return
    mid=reaction.message.id
    if mid in first_react_announced: return
    first_react_announced.add(mid)
    try:
        msg = _cmsg(reaction.message.guild.id,"FIRST_REACT_MSG").format(mention=user.mention,user=str(user))
        await reaction.message.channel.send(msg,allowed_mentions=discord.AllowedMentions(users=True))
    except: pass

# ================================================================
#  TICKET SYSTEM
# ================================================================

def is_ticket(ch)->bool:
    if not hasattr(ch,"guild"): return False
    return ch.category_id==_cid(ch.guild.id,"TICKET_CATEGORY_ID") and ch.name.startswith("ticket-")
def can_ticket(m)->bool:
    if m.id in OWNERS: return True
    sid=_cid(m.guild.id,"SUPPORT_ROLE_ID")
    return any(r.id==sid or r.permissions.administrator for r in m.roles)


class TicketActionView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="tkt_close")
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        if not can_ticket(interaction.user):
            return await interaction.response.send_message("Du hast keine Berechtigung, Tickets zu schließen.", ephemeral=True)
        await interaction.response.defer()
        sr = interaction.guild.get_role(_cid(interaction.guild.id, "SUPPORT_ROLE_ID"))
        try:
            await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
            if sr:
                await interaction.channel.set_permissions(sr, read_messages=True, send_messages=True)
            embed = discord.Embed(
                description=_cmsg(interaction.guild.id,"TICKET_CLOSE_MSG"),
                color=0x2B2D31)
            await interaction.channel.send(embed=embed, view=TicketDeleteView())
        except Exception as e:
            await interaction.followup.send(f"Ein Fehler ist aufgetreten: {e}", ephemeral=True)

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger, custom_id="tkt_delete")
    async def delete_btn(self, interaction: discord.Interaction, button: Button):
        if not can_ticket(interaction.user):
            return await interaction.response.send_message("Du hast keine Berechtigung, Tickets zu löschen.", ephemeral=True)
        await interaction.response.defer()
        embed = discord.Embed(description=_cmsg(interaction.guild.id,"TICKET_DELETE_MSG"), color=0x2B2D31)
        try:
            await interaction.channel.send(embed=embed)
        except: pass
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Gelöscht von {interaction.user}")
        except: pass


class TicketDeleteView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger, custom_id="tkt_delete_v2")
    async def delete_btn(self, interaction: discord.Interaction, button: Button):
        if not can_ticket(interaction.user):
            return await interaction.response.send_message("Du hast keine Berechtigung, Tickets zu löschen.", ephemeral=True)
        await interaction.response.defer()
        embed = discord.Embed(description=_cmsg(interaction.guild.id,"TICKET_DELETE_MSG"), color=0x2B2D31)
        try:
            await interaction.channel.send(embed=embed)
        except: pass
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Gelöscht von {interaction.user}")
        except: pass


class TicketButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ticket öffnen", style=discord.ButtonStyle.blurple, custom_id="tkt_create")
    async def create(self, interaction: discord.Interaction, button: Button):
        g = interaction.guild
        cat = g.get_channel(_cid(g.id, "TICKET_CATEGORY_ID"))
        if cat:
            for ch in cat.text_channels:
                if ch.name.startswith("ticket-") and ch.overwrites_for(interaction.user).read_messages:
                    return await interaction.response.send_message(
                        f"Du hast bereits ein offenes Ticket: {ch.mention}", ephemeral=True)
        num = _tkt_num(g.id)
        sr  = g.get_role(_cid(g.id, "SUPPORT_ROLE_ID"))
        ow  = {
            g.default_role:   discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if sr:
            ow[sr] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        for r in g.roles:
            if r.permissions.administrator and r not in ow:
                ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        try:
            tc = await g.create_text_channel(
                name=f"ticket-{num}", category=cat, overwrites=ow,
                reason=f"Ticket geöffnet von {interaction.user}")
            open_msg = _cmsg(g.id, "TICKET_OPEN_MSG")
            embed = discord.Embed(
                title=f"Ticket #{num}",
                description=open_msg,
                color=0x2B2D31,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Geöffnet von {interaction.user}", icon_url=interaction.user.display_avatar.url)
            pings = f"{sr.mention} {interaction.user.mention}" if sr else interaction.user.mention
            await tc.send(
                content=pings, embed=embed, view=TicketActionView(),
                allowed_mentions=discord.AllowedMentions(roles=True, users=True))
            await interaction.response.send_message(
                f"Dein Ticket wurde erstellt: {tc.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Ein Fehler ist aufgetreten: {e}", ephemeral=True)


@bot.command()
async def close(ctx: commands.Context):
    if ctx.guild.id != ALLOWED_GUILD_ID or not is_ticket(ctx.channel): return
    if not can_ticket(ctx.author):
        return await ctx.send("Du hast keine Berechtigung, Tickets zu schließen.")
    try: await ctx.message.delete()
    except: pass
    sr = ctx.guild.get_role(_cid(ctx.guild.id, "SUPPORT_ROLE_ID"))
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, read_messages=False, send_messages=False)
        if sr:
            await ctx.channel.set_permissions(sr, read_messages=True, send_messages=True)
        embed = discord.Embed(
            description=_cmsg(ctx.guild.id,"TICKET_CLOSE_MSG"),
            color=0x2B2D31)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Ein Fehler ist aufgetreten: {e}")

@bot.command(name="delete")
async def delete_ticket(ctx: commands.Context):
    if ctx.guild.id != ALLOWED_GUILD_ID or not is_ticket(ctx.channel): return
    if not can_ticket(ctx.author):
        return await ctx.send("Du hast keine Berechtigung, Tickets zu löschen.")
    try: await ctx.message.delete()
    except: pass
    embed = discord.Embed(description=_cmsg(ctx.guild.id,"TICKET_DELETE_MSG"), color=0x2B2D31)
    try: await ctx.send(embed=embed)
    except: pass
    await asyncio.sleep(3)
    try:
        await ctx.channel.delete(reason=f"Gelöscht von {ctx.author}")
    except Exception as e:
        await ctx.send(f"Ein Fehler ist aufgetreten: {e}")

# ================================================================
#  /setup — Ticket System
# ================================================================

class SetupMainView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.select(
        placeholder="Option auswählen...",
        options=[
            discord.SelectOption(label="Panel-Kanal", value="panel_channel", description="Kanal für das Ticket-Panel."),
            discord.SelectOption(label="Ticket-Kategorie", value="ticket_category", description="Kategorie für Ticket-Kanäle."),
            discord.SelectOption(label="Support-Rolle", value="support_role", description="Rolle für Ticket-Management."),
            discord.SelectOption(label="Panel-Nachricht", value="panel_message", description="Text des Ticket-Panels."),
            discord.SelectOption(label="Ticket-Öffnungsnachricht", value="ticket_open_msg", description="Nachricht bei Ticket-Öffnung."),
            discord.SelectOption(label="Panel senden", value="send_panel", description="Panel in den konfigurierten Kanal senden."),
            discord.SelectOption(label="Statistiken", value="statistics", description="Ticket-Statistiken anzeigen."),
        ],
        min_values=1, max_values=1, custom_id="setup_main_select"
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        g = interaction.guild

        if value == "panel_channel":
            view = discord.ui.View(timeout=60)
            class _PanelChSel(discord.ui.ChannelSelect):
                def __init__(self_inner):
                    super().__init__(placeholder="Panel-Kanal auswählen...", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
                async def callback(self_inner, i2: discord.Interaction):
                    ch = self_inner.values[0]; _sid(i2.guild.id, "TICKET_PANEL_CHANNEL_ID", ch.id)
                    await mlog(i2.guild, "Setup", f"{i2.user} hat Panel-Kanal auf #{ch.name} ({ch.id}) gesetzt.")
                    await _return_to_setup(i2, f"Panel-Kanal wurde auf {ch.mention} gesetzt.")
            view.add_item(_PanelChSel())
            embed = discord.Embed(title="Setup — Panel-Kanal", description="Kanal auswählen, in dem das Ticket-Panel erscheint.", color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=view)

        elif value == "ticket_category":
            view = discord.ui.View(timeout=60)
            class _CatSel(discord.ui.ChannelSelect):
                def __init__(self_inner):
                    super().__init__(placeholder="Ticket-Kategorie auswählen...", channel_types=[discord.ChannelType.category], min_values=1, max_values=1)
                async def callback(self_inner, i2: discord.Interaction):
                    cat = self_inner.values[0]; _sid(i2.guild.id, "TICKET_CATEGORY_ID", cat.id)
                    await mlog(i2.guild, "Setup", f"{i2.user} hat Ticket-Kategorie auf {cat.name} gesetzt.")
                    await _return_to_setup(i2, f"Ticket-Kategorie wurde auf **{cat.name}** gesetzt.")
            view.add_item(_CatSel())
            embed = discord.Embed(title="Setup — Ticket-Kategorie", description="Kategorie auswählen, in der Ticket-Kanäle erstellt werden.", color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=view)

        elif value == "support_role":
            view = discord.ui.View(timeout=60)
            class _SupportRoleSel(discord.ui.RoleSelect):
                def __init__(self_inner):
                    super().__init__(placeholder="Support-Rolle auswählen...", min_values=1, max_values=1)
                async def callback(self_inner, i2: discord.Interaction):
                    role = self_inner.values[0]; _sid(i2.guild.id, "SUPPORT_ROLE_ID", role.id)
                    await mlog(i2.guild, "Setup", f"{i2.user} hat Support-Rolle auf {role.name} gesetzt.")
                    await _return_to_setup(i2, f"Support-Rolle wurde auf {role.mention} gesetzt.")
            view.add_item(_SupportRoleSel())
            embed = discord.Embed(title="Setup — Support-Rolle", description="Rolle auswählen, die Zugriff auf alle Tickets hat.", color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=view)

        elif value == "panel_message":
            current = _cmsg(g.id, "TICKET_PANEL_DESC")
            await interaction.response.send_modal(_SetupTextModal("TICKET_PANEL_DESC", "Panel-Nachricht", current, placeholder="Text des Ticket-Panels..."))

        elif value == "ticket_open_msg":
            current = _cmsg(g.id, "TICKET_OPEN_MSG")
            await interaction.response.send_modal(_SetupTextModal("TICKET_OPEN_MSG", "Ticket-Öffnungsnachricht", current, placeholder="Nachricht bei Ticket-Öffnung..."))

        elif value == "send_panel":
            if interaction.user.id not in OWNERS:
                return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
            ch = g.get_channel(_cid(g.id, "TICKET_PANEL_CHANNEL_ID"))
            if not ch:
                embed = discord.Embed(title="Setup — Panel senden", description="**Kein Panel-Kanal konfiguriert.**\nBitte zuerst den Panel-Kanal einstellen.", color=0x2B2D31)
                return await interaction.response.edit_message(embed=embed, view=_BackToSetupView(g.id))
            panel_desc = _cmsg(g.id, "TICKET_PANEL_DESC")
            embed = discord.Embed(title="Support", description=panel_desc, color=0x2B2D31)
            try:
                await ch.send(embed=embed, view=TicketButton())
                await _return_to_setup(interaction, f"Ticket-Panel wurde in {ch.mention} gesendet.")
            except Exception as e:
                await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

        elif value == "statistics":
            total   = _tkt_cur(g.id)
            cat     = g.get_channel(_cid(g.id, "TICKET_CATEGORY_ID"))
            open_t  = len([c for c in cat.text_channels if c.name.startswith("ticket-")]) if cat else 0
            panel_ch   = g.get_channel(_cid(g.id, "TICKET_PANEL_CHANNEL_ID"))
            ticket_cat = g.get_channel(_cid(g.id, "TICKET_CATEGORY_ID"))
            sr_id      = _cid(g.id, "SUPPORT_ROLE_ID")
            desc = (f"**Tickets gesamt** — {total}\n**Aktuell offen** — {open_t}\n**Nächste Nummer** — {total + 1}\n\n"
                    f"**Panel-Kanal** — {panel_ch.mention if panel_ch else 'Nicht konfiguriert'}\n"
                    f"**Ticket-Kategorie** — {ticket_cat.name if ticket_cat else 'Nicht konfiguriert'}\n"
                    f"**Support-Rolle** — {f'<@&{sr_id}>' if sr_id else 'Nicht konfiguriert'}")
            embed = discord.Embed(title="Ticket-Statistiken", description=desc, color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=_BackToSetupView(g.id))


async def _return_to_setup(interaction: discord.Interaction, notice: str = ""):
    g = interaction.guild
    cat_id   = _cid(g.id, "TICKET_CATEGORY_ID"); panel_id = _cid(g.id, "TICKET_PANEL_CHANNEL_ID"); sr_id = _cid(g.id, "SUPPORT_ROLE_ID")
    cat_ch   = g.get_channel(cat_id); panel_ch = g.get_channel(panel_id)
    desc = ((f"{notice}\n\n" if notice else "") + "Ticket-System über das Menü konfigurieren.\n\n"
            f"**Panel-Kanal** — {panel_ch.mention if panel_ch else 'Nicht konfiguriert'}\n"
            f"**Ticket-Kategorie** — {cat_ch.name if cat_ch else 'Nicht konfiguriert'}\n"
            f"**Support-Rolle** — {f'<@&{sr_id}>' if sr_id else 'Nicht konfiguriert'}")
    embed = discord.Embed(title="Ticket-System Setup", description=desc, color=0x2B2D31)
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.edit_message(embed=embed, view=SetupMainView(g.id))

class _BackToSetupView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=120); self.guild_id = guild_id
    @discord.ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _return_to_setup(interaction)

class _SetupTextModal(discord.ui.Modal):
    def __init__(self, key: str, label: str, current: str, placeholder: str = ""):
        super().__init__(title=f"Bearbeiten — {label}"); self.key = key
        self.field = discord.ui.TextInput(label=label[:45], style=discord.TextStyle.paragraph,
            default=current[:4000], max_length=4000, placeholder=placeholder)
        self.add_item(self.field)
    async def on_submit(self, interaction: discord.Interaction):
        _smsg(interaction.guild.id, self.key, self.field.value)
        await _return_to_setup(interaction, f"**{self.title.replace('Bearbeiten — ', '')}** wurde aktualisiert.")

@bot.tree.command(name="setup", description="Ticket-System konfigurieren.", guild=discord.Object(id=ALLOWED_GUILD_ID))
async def setup_cmd(interaction: discord.Interaction):
    if interaction.user.id not in OWNERS:
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    g = interaction.guild
    cat_id   = _cid(g.id, "TICKET_CATEGORY_ID"); panel_id = _cid(g.id, "TICKET_PANEL_CHANNEL_ID"); sr_id = _cid(g.id, "SUPPORT_ROLE_ID")
    cat_ch   = g.get_channel(cat_id); panel_ch = g.get_channel(panel_id)
    desc = ("Ticket-System über das Menü konfigurieren.\n\n"
            f"**Panel-Kanal** — {panel_ch.mention if panel_ch else 'Nicht konfiguriert'}\n"
            f"**Ticket-Kategorie** — {cat_ch.name if cat_ch else 'Nicht konfiguriert'}\n"
            f"**Support-Rolle** — {f'<@&{sr_id}>' if sr_id else 'Nicht konfiguriert'}")
    embed = discord.Embed(title="Ticket-System Setup", description=desc, color=0x2B2D31)
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=SetupMainView(g.id), ephemeral=True)

# ================================================================
#  /config
# ================================================================

class ConfigMainView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300); self.guild_id = guild_id

    @discord.ui.select(
        placeholder="Einstellung auswählen...",
        options=[
            discord.SelectOption(label="Willkommens-Kanal",  value="WELCOME_CHANNEL_ID",  description="Kanal für neue Mitglieder."),
            discord.SelectOption(label="Log-Kanal",          value="LOG_CHANNEL_ID",       description="Kanal für Mod- und Security-Logs."),
            discord.SelectOption(label="Regeln-Kanal",       value="RULES_CHANNEL_ID",     description="Regeln-Kanal für Willkommensnachricht."),
            discord.SelectOption(label="Boost-Kanal",        value="BOOST_CHANNEL_ID",     description="Kanal für Boost-Benachrichtigungen."),
            discord.SelectOption(label="Invite-Kanal",       value="INVITE_CHANNEL_ID",    description="Kanal für Einladungs-Tracking."),
            discord.SelectOption(label="Auto-Rolle",         value="AUTO_ROLE_ID",         description="Automatisch zugewiesene Rolle beim Beitritt."),
            discord.SelectOption(label="Trigger-Rolle",      value="TRIGGER_ROLE_ID",      description="Rolle, die weitere Rollen auslöst."),
            discord.SelectOption(label="Timeout-Rolle",      value="TIMEOUT_ROLE_ID",      description="Rolle mit Timeout-Berechtigung."),
            discord.SelectOption(label="Willkommensnachricht", value="WELCOME_MSG",        description="Willkommensnachricht bearbeiten."),
            discord.SelectOption(label="Boost-Nachricht",    value="BOOST_MSG",            description="Boost-Nachricht bearbeiten."),
        ],
        min_values=1, max_values=1, custom_id="config_main_select"
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        key = select.values[0]
        channel_keys = {"WELCOME_CHANNEL_ID":("Willkommens-Kanal",[discord.ChannelType.text]),"LOG_CHANNEL_ID":("Log-Kanal",[discord.ChannelType.text]),"RULES_CHANNEL_ID":("Regeln-Kanal",[discord.ChannelType.text]),"BOOST_CHANNEL_ID":("Boost-Kanal",[discord.ChannelType.text]),"INVITE_CHANNEL_ID":("Invite-Kanal",[discord.ChannelType.text])}
        role_keys = {"AUTO_ROLE_ID":"Auto-Rolle","TRIGGER_ROLE_ID":"Trigger-Rolle","TIMEOUT_ROLE_ID":"Timeout-Rolle"}
        message_keys = {"WELCOME_MSG":"Willkommensnachricht","BOOST_MSG":"Boost-Nachricht"}

        if key in channel_keys:
            label, ch_types = channel_keys[key]
            view = discord.ui.View(timeout=60)
            class _ChSel(discord.ui.ChannelSelect):
                def __init__(self_inner):
                    super().__init__(placeholder=f"{label} auswählen...", channel_types=ch_types, min_values=1, max_values=1)
                async def callback(self_inner, i2: discord.Interaction):
                    ch = self_inner.values[0]; _sid(i2.guild.id, key, ch.id)
                    await mlog(i2.guild, "Config", f"{i2.user} hat `{key}` auf #{ch.name} gesetzt.")
                    await _return_to_config(i2, f"**{label}** wurde auf {ch.mention} gesetzt.")
            view.add_item(_ChSel())
            embed = discord.Embed(title=f"Config — {label}", description=f"Kanal für **{label}** auswählen.", color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=view)
        elif key in role_keys:
            label = role_keys[key]
            view = discord.ui.View(timeout=60)
            class _RoleSel(discord.ui.RoleSelect):
                def __init__(self_inner):
                    super().__init__(placeholder=f"{label} auswählen...", min_values=1, max_values=1)
                async def callback(self_inner, i2: discord.Interaction):
                    role = self_inner.values[0]; _sid(i2.guild.id, key, role.id)
                    await mlog(i2.guild, "Config", f"{i2.user} hat `{key}` auf {role.name} gesetzt.")
                    await _return_to_config(i2, f"**{label}** wurde auf {role.mention} gesetzt.")
            view.add_item(_RoleSel())
            embed = discord.Embed(title=f"Config — {label}", description=f"Rolle für **{label}** auswählen.", color=0x2B2D31)
            await interaction.response.edit_message(embed=embed, view=view)
        elif key in message_keys:
            label = message_keys[key]
            current = _cmsg(interaction.guild.id, key)
            await interaction.response.send_modal(_ConfigTextModal(key, label, current))


async def _return_to_config(interaction: discord.Interaction, notice: str = ""):
    g = interaction.guild
    ids = {k: _cid(g.id, k) for k in ["WELCOME_CHANNEL_ID","LOG_CHANNEL_ID","RULES_CHANNEL_ID","BOOST_CHANNEL_ID","INVITE_CHANNEL_ID","AUTO_ROLE_ID","TRIGGER_ROLE_ID","TIMEOUT_ROLE_ID"]}
    def ch(k): return f"<#{ids[k]}>" if ids[k] else "Nicht konfiguriert"
    def ro(k): return f"<@&{ids[k]}>" if ids[k] else "Nicht konfiguriert"
    desc = ((f"{notice}\n\n" if notice else "") + "Bot-Einstellungen über das Menü konfigurieren.\n\n"
            + "\n".join([
                f"**Willkommens-Kanal** — {ch('WELCOME_CHANNEL_ID')}",
                f"**Log-Kanal** — {ch('LOG_CHANNEL_ID')}",
                f"**Regeln-Kanal** — {ch('RULES_CHANNEL_ID')}",
                f"**Boost-Kanal** — {ch('BOOST_CHANNEL_ID')}",
                f"**Invite-Kanal** — {ch('INVITE_CHANNEL_ID')}",
                f"**Auto-Rolle** — {ro('AUTO_ROLE_ID')}",
                f"**Trigger-Rolle** — {ro('TRIGGER_ROLE_ID')}",
                f"**Timeout-Rolle** — {ro('TIMEOUT_ROLE_ID')}",
            ]))
    embed = discord.Embed(title="Bot-Konfiguration", description=desc, color=0x2B2D31)
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.edit_message(embed=embed, view=ConfigMainView(g.id))

class _ConfigTextModal(discord.ui.Modal):
    def __init__(self, key: str, label: str, current: str):
        super().__init__(title=f"Bearbeiten — {label}"); self.key = key
        self.field = discord.ui.TextInput(label=label[:45], style=discord.TextStyle.paragraph,
            default=current[:4000], max_length=4000, placeholder="Nachrichtentext eingeben...")
        self.add_item(self.field)
    async def on_submit(self, interaction: discord.Interaction):
        _smsg(interaction.guild.id, self.key, self.field.value)
        await _return_to_config(interaction, f"**{self.title.replace('Bearbeiten — ', '')}** wurde aktualisiert.")

@bot.tree.command(name="config", description="Bot-Einstellungen konfigurieren.", guild=discord.Object(id=ALLOWED_GUILD_ID))
async def config_cmd(interaction: discord.Interaction):
    if interaction.user.id not in OWNERS:
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    g = interaction.guild
    ids = {k: _cid(g.id, k) for k in ["WELCOME_CHANNEL_ID","LOG_CHANNEL_ID","RULES_CHANNEL_ID","BOOST_CHANNEL_ID","INVITE_CHANNEL_ID","AUTO_ROLE_ID","TRIGGER_ROLE_ID","TIMEOUT_ROLE_ID"]}
    def ch(k): return f"<#{ids[k]}>" if ids[k] else "Nicht konfiguriert"
    def ro(k): return f"<@&{ids[k]}>" if ids[k] else "Nicht konfiguriert"
    desc = ("Bot-Einstellungen über das Menü konfigurieren.\n\n"
            + "\n".join([
                f"**Willkommens-Kanal** — {ch('WELCOME_CHANNEL_ID')}",
                f"**Log-Kanal** — {ch('LOG_CHANNEL_ID')}",
                f"**Regeln-Kanal** — {ch('RULES_CHANNEL_ID')}",
                f"**Boost-Kanal** — {ch('BOOST_CHANNEL_ID')}",
                f"**Invite-Kanal** — {ch('INVITE_CHANNEL_ID')}",
                f"**Auto-Rolle** — {ro('AUTO_ROLE_ID')}",
                f"**Trigger-Rolle** — {ro('TRIGGER_ROLE_ID')}",
                f"**Timeout-Rolle** — {ro('TIMEOUT_ROLE_ID')}",
            ]))
    embed = discord.Embed(title="Bot-Konfiguration", description=desc, color=0x2B2D31)
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=ConfigMainView(g.id), ephemeral=True)

# ================================================================
#  TICKET MANAGEMENT COMMANDS
# ================================================================

@bot.tree.command(name="adduser", description="Mitglied zum aktuellen Ticket hinzufügen.", guild=discord.Object(id=ALLOWED_GUILD_ID))
async def adduser(interaction: discord.Interaction, member: discord.Member):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Nur in Ticket-Kanälen verfügbar.", ephemeral=True)
    if not can_ticket(interaction.user):
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    try:
        await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"{member.mention} wurde zum Ticket hinzugefügt.")
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

@bot.tree.command(name="removeuser", description="Mitglied aus dem aktuellen Ticket entfernen.", guild=discord.Object(id=ALLOWED_GUILD_ID))
async def removeuser(interaction: discord.Interaction, member: discord.Member):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Nur in Ticket-Kanälen verfügbar.", ephemeral=True)
    if not can_ticket(interaction.user):
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    try:
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"{member.mention} wurde aus dem Ticket entfernt.")
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

@bot.tree.command(name="renameticket", description="Aktuellen Ticket-Kanal umbenennen.", guild=discord.Object(id=ALLOWED_GUILD_ID))
async def renameticket(interaction: discord.Interaction, name: str):
    if not is_ticket(interaction.channel):
        return await interaction.response.send_message("Nur in Ticket-Kanälen verfügbar.", ephemeral=True)
    if not can_ticket(interaction.user):
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    name = name.lower().replace(" ", "-")[:50]
    try:
        await interaction.channel.edit(name=f"ticket-{name}")
        await interaction.response.send_message(f"Ticket umbenannt zu **ticket-{name}**.")
    except Exception as e:
        await interaction.response.send_message(f"Fehler: {e}", ephemeral=True)

# ================================================================
#  /security_config — Configure every security punishment
# ================================================================

class SecurityConfigView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.select(
        placeholder="Security-Event auswählen...",
        options=[discord.SelectOption(label=label, value=event, description=f"Aktuell: ...") for event, label in SEC_PUNISHMENT_LABELS.items()],
        min_values=1, max_values=1,
        custom_id="sec_cfg_event_sel"
    )
    async def event_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        event = select.values[0]
        current = _sec_punishment_get(interaction.guild.id, event)
        label = SEC_PUNISHMENT_LABELS.get(event, event)

        pun_view = PunishmentSelectView(self.guild_id, event, label, current)
        embed = discord.Embed(
            title=f" Security Config — {label}",
            description=(
                f"**Aktuell:** `{current}`\n\n"
                "**Strafe auswählen:**\n"
                " `none` — Nur blockieren/löschen, keine Strafe\n"
                " `clear_roles` — Alle Rollen entfernen\n"
                "⏱ `timeout` — Timeout (5 Minuten)\n"
                " `kick` — Vom Server kicken\n"
                " `ban` — Vom Server bannen"
            ),
            color=0x2B2D31
        )
        await interaction.response.edit_message(embed=embed, view=pun_view)

class PunishmentSelectView(discord.ui.View):
    def __init__(self, guild_id: int, event: str, event_label: str, current: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.event = event
        self.event_label = event_label
        self.current = current

    @discord.ui.select(
        placeholder="Strafe auswählen...",
        options=[
            discord.SelectOption(label=" Keine Strafe (nur blockieren)", value="none"),
            discord.SelectOption(label=" Rollen entfernen (clear_roles)", value="clear_roles"),
            discord.SelectOption(label="⏱ Timeout", value="timeout"),
            discord.SelectOption(label=" Kick", value="kick"),
            discord.SelectOption(label=" Ban", value="ban"),
        ],
        min_values=1, max_values=1,
        custom_id="sec_cfg_pun_sel"
    )
    async def pun_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        new_pun = select.values[0]
        _sec_punishment_set(interaction.guild.id, self.event, new_pun)
        await mlog(interaction.guild, "Security Config",
                   f"{interaction.user} hat die Strafe für **{self.event_label}** auf `{new_pun}` gesetzt.")
        await _return_to_sec_config(interaction,
            f" Strafe für **{self.event_label}** wurde auf `{new_pun}` gesetzt.")

    @discord.ui.button(label="← Zurück", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _return_to_sec_config(interaction)

async def _return_to_sec_config(interaction: discord.Interaction, notice: str = ""):
    g = interaction.guild
    lines = []
    for event, label in SEC_PUNISHMENT_LABELS.items():
        current = _sec_punishment_get(g.id, event)
        default = SEC_PUNISHMENT_DEFAULTS.get(event, "ban")
        marker = "" if current == default else " "
        lines.append(f"`{current}`{marker} — {label}")

    embed = discord.Embed(
        title=" Security Config — Strafen",
        description=(
            (f"{notice}\n\n" if notice else "") +
            "Hier kannst du für jedes Security-Event die Strafe einstellen.\n"
            " = von Standard abweichend\n\n" +
            "\n".join(lines)
        ),
        color=0x2B2D31
    )
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.edit_message(embed=embed, view=SecurityConfigView(g.id))

@bot.tree.command(name="security_config", description="Security-Strafen für jeden Event einzeln konfigurieren.",
    guild=discord.Object(id=ALLOWED_GUILD_ID))
async def security_config_cmd(interaction: discord.Interaction):
    if interaction.user.id not in OWNERS:
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)

    g = interaction.guild
    lines = []
    for event, label in SEC_PUNISHMENT_LABELS.items():
        current = _sec_punishment_get(g.id, event)
        default = SEC_PUNISHMENT_DEFAULTS.get(event, "ban")
        marker = "" if current == default else " "
        lines.append(f"`{current}`{marker} — {label}")

    embed = discord.Embed(
        title=" Security Config — Strafen",
        description=(
            "Hier kannst du für jedes Security-Event die Strafe einstellen.\n"
            "Wähle ein Event aus dem Dropdown, um die Strafe zu ändern.\n"
            " = von Standard abweichend\n\n" +
            "\n".join(lines)
        ),
        color=0x2B2D31
    )
    embed.set_footer(text=f"Von {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=SecurityConfigView(g.id), ephemeral=True)

# ================================================================
#  MODERATION — PREFIX COMMANDS
# ================================================================

@bot.command()
async def kick(ctx:commands.Context,member:discord.Member=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?kick @user [Grund]`")
    if member.id in OWNERS: return await clean(ctx,"Du kannst keinen Server-Owner kicken.")
    if member.top_role>=ctx.guild.me.top_role: return await clean(ctx,"Diese Person hat eine höhere Rolle als ich.")
    try:
        await member.kick(reason=f"{ctx.author}: {reason}")
        await ctx.send(f"**{member}** wurde von **{ctx.author.name}** gekickt. | {reason}")
        await mlog(ctx.guild,"Kick",f"{ctx.author} hat {member} ({member.id}) gekickt. Grund: {reason}")
    except discord.Forbidden: await ctx.send("Ich habe keine Berechtigung, diese Person zu kicken.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command()
async def ban(ctx:commands.Context,member:discord.Member=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?ban @user [Grund]`")
    if member.id in OWNERS: return await clean(ctx,"Du kannst keinen Server-Owner bannen.")
    if member.top_role>=ctx.guild.me.top_role: return await clean(ctx,"Diese Person hat eine höhere Rolle als ich.")
    try:
        await member.ban(reason=f"{ctx.author}: {reason}",delete_message_days=1)
        await ctx.send(f"**{member}** wurde von **{ctx.author.name}** gebannt. | {reason}")
        await mlog(ctx.guild,"Ban",f"{ctx.author} hat {member} ({member.id}) gebannt. Grund: {reason}")
    except discord.Forbidden: await ctx.send("Ich habe keine Berechtigung, diese Person zu bannen.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command()
async def unban(ctx:commands.Context,user_id:str=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not user_id or not user_id.isdigit(): return await clean(ctx,"Verwendung: `?unban <user_id> [Grund]`")
    try:
        user=await bot.fetch_user(int(user_id)); await ctx.guild.unban(user,reason=f"{ctx.author}: {reason}")
        await ctx.send(f"**{user}** wurde entbannt.")
        await mlog(ctx.guild,"Unban",f"{ctx.author} hat {user} ({user.id}) entbannt.")
    except discord.NotFound: await ctx.send("Benutzer nicht gefunden oder nicht gebannt.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command(aliases=["to"])
async def timeout(ctx:commands.Context,member:discord.Member=None,duration:str=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_timeout(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member or not duration: return await clean(ctx,"Verwendung: `?timeout @user <Dauer> [Grund]` — z.B. 10m, 2h, 1d")
    secs=parse_time(duration)
    if secs is None: return await clean(ctx,"Ungültige Dauer. Beispiele: `10m`, `2h`, `1d`")
    if secs>2419200: return await clean(ctx,"Maximale Timeout-Dauer ist 28 Tage.")
    try:
        until=discord.utils.utcnow()+timedelta(seconds=secs)
        await member.timeout(until,reason=f"{ctx.author}: {reason}")
        await ctx.send(f"**{member}** wurde für **{duration}** getimeouted. | {reason}")
        await mlog(ctx.guild,"Timeout",f"{ctx.author} hat {member} ({member.id}) für {duration} getimeouted.")
    except discord.Forbidden: await ctx.send("Keine Berechtigung.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command(name="rto")
async def rto(ctx:commands.Context,member:discord.Member=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_timeout(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?rto @user`")
    try:
        await member.timeout(None,reason=f"Timeout entfernt von {ctx.author}")
        await ctx.send(f"Timeout für **{member}** wurde entfernt.")
        await mlog(ctx.guild,"Timeout Entfernt",f"{ctx.author} hat Timeout von {member} ({member.id}) entfernt.")
    except discord.Forbidden: await ctx.send("Keine Berechtigung.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command()
async def warn(ctx:commands.Context,member:discord.Member=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?warn @user [Grund]`")
    wid=_warn_add(ctx.guild.id,member.id,ctx.author.id,reason); wl=_warn_get(ctx.guild.id,member.id)
    await ctx.send(f"**{member}** wurde verwarnt (#{wid}, gesamt: {len(wl)}). | {reason}")
    await mlog(ctx.guild,"Verwarnung",f"{ctx.author} hat {member} ({member.id}) verwarnt — #{wid}. {reason}")
    try:
        await member.send(embed=discord.Embed(title=f"Verwarnung — {ctx.guild.name}",
            description=f"**Grund:** {reason}\n**Verwarnung #{wid}** — Gesamt: {len(wl)}",
            color=discord.Color.yellow(),timestamp=datetime.utcnow()))
    except: pass

@bot.command()
async def warns(ctx:commands.Context,member:discord.Member=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?warns @user`")
    wl=_warn_get(ctx.guild.id,member.id)
    embed=discord.Embed(title=f"Verwarnungen — {member}",color=discord.Color.yellow(),timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    if not wl: embed.description="Keine Verwarnungen."
    else:
        for wid,mid,r,ts in wl:
            m=ctx.guild.get_member(mid)
            embed.add_field(name=f"#{wid} — {ts[:10]}",value=f"**Grund:** {r}\n**Moderator:** {str(m) if m else mid}",inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def clearwarn(ctx:commands.Context,warn_id:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not warn_id or not warn_id.isdigit(): return await clean(ctx,"Verwendung: `?clearwarn <id>`")
    if _warn_del(int(warn_id),ctx.guild.id): await ctx.send(f"Verwarnung **#{warn_id}** wurde entfernt.",delete_after=5)
    else: await ctx.send(f"Verwarnung **#{warn_id}** nicht gefunden.",delete_after=5)

@bot.command()
async def clearwarns(ctx:commands.Context,member:discord.Member=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?clearwarns @user`")
    n=_warn_clear(ctx.guild.id,member.id)
    await ctx.send(f"**{n}** Verwarnung(en) für {member.mention} gelöscht.",delete_after=5)

@bot.command()
async def purge(ctx:commands.Context,amount:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    try: await ctx.message.delete()
    except: pass
    if not amount: return await ctx.send("Verwendung: `?purge all` oder `?purge <Anzahl>`",delete_after=3)
    if amount.lower()=="all": deleted=await ctx.channel.purge(limit=None)
    else:
        if not amount.isdigit() or int(amount)<1: return await ctx.send("Ungültige Anzahl.",delete_after=3)
        if int(amount)>1000: return await ctx.send("Maximum ist 1000 Nachrichten.",delete_after=3)
        deleted=await ctx.channel.purge(limit=int(amount))
    n=await ctx.send(f"{len(deleted)} Nachricht(en) gelöscht."); await asyncio.sleep(3)
    try: await n.delete()
    except: pass

@bot.command(name="slowmode")
async def slowmode_cmd(ctx:commands.Context,seconds:int=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if seconds is None: return await clean(ctx,"Verwendung: `?slowmode <Sekunden>` (0 = deaktivieren)")
    if seconds<0 or seconds>21600: return await clean(ctx,"Wert muss zwischen 0 und 21600 liegen.")
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send("Slowmode deaktiviert." if seconds==0 else f"Slowmode auf **{seconds}s** gesetzt.",delete_after=5)
        try: await ctx.message.delete()
        except: pass
    except Exception as e: await ctx.send(f"Fehler: {e}")

# ================================================================
#  ROLES — PREFIX
# ================================================================

_PERMS_LIST=["administrator","manage_guild","manage_roles","manage_channels","manage_messages",
    "manage_nicknames","kick_members","ban_members","moderate_members","view_audit_log",
    "mention_everyone","send_messages","read_messages","attach_files","embed_links",
    "add_reactions","use_external_emojis","connect","speak","move_members",
    "mute_members","deafen_members","create_instant_invite"]

class RoleCreateView(discord.ui.View):
    def __init__(self,ctx,name,color,hoist):
        super().__init__(timeout=120); self.ctx=ctx; self.name=name; self.color=color; self.hoist=hoist; self.chosen=set()
        options=[discord.SelectOption(label=p.replace("_"," ").title(),value=p) for p in _PERMS_LIST]
        sel=discord.ui.Select(placeholder="Berechtigungen auswählen (optional)...",options=options,min_values=0,max_values=len(options),custom_id="rc_perms")
        sel.callback=self.perms_cb; self.add_item(sel)
    async def perms_cb(self,interaction:discord.Interaction): self.chosen=set(interaction.data["values"]); await interaction.response.defer()
    @discord.ui.button(label="Rolle erstellen",style=discord.ButtonStyle.success,row=1)
    async def confirm(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.ctx.author.id: return await interaction.response.send_message("Dieses Menü ist nicht für dich.",ephemeral=True)
        perms=discord.Permissions()
        for p in self.chosen:
            if hasattr(perms,p): setattr(perms,p,True)
        try:
            role=await interaction.guild.create_role(name=self.name,permissions=perms,color=self.color,hoist=self.hoist,reason=f"?rolecreate von {interaction.user}")
            for ch in interaction.guild.channels:
                try:
                    if ch.overwrites_for(role).is_empty():
                        await ch.set_permissions(role,view_channel=True,reason="Rolle erstellt — Standardberechtigung")
                except: pass
            await interaction.response.edit_message(content=f"Rolle **{role.name}** erstellt ({role.mention}) mit {len(self.chosen)} Berechtigung(en).",view=None)
            await mlog(interaction.guild,"Rolle Erstellt",f"{interaction.user} hat **{role.name}** erstellt. Berechtigungen: {', '.join(self.chosen) or 'keine'}")
        except Exception as e: await interaction.response.edit_message(content=f"Fehler: {e}",view=None)
    @discord.ui.button(label="Abbrechen",style=discord.ButtonStyle.danger,row=1)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button): await interaction.response.edit_message(content="Abgebrochen.",view=None)

@bot.command(name="role")
async def role_cmd(ctx:commands.Context,member:discord.Member=None,*,role_input:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_role(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member or not role_input: return await clean(ctx,"Verwendung: `?role @user <Rollenname oder ID>`")
    matches=find_role(ctx.guild,role_input)
    if not matches: return await clean(ctx,f"Keine Rolle gefunden: **{role_input}**.")
    if len(matches)>1: return await clean(ctx,f"Mehrere Rollen gefunden: {', '.join(f'`{r.name}`' for r in matches[:6])} — bitte präziser.",delay=6)
    role=matches[0]
    if role>=ctx.guild.me.top_role: return await clean(ctx,"Diese Rolle ist gleich oder höher als meine höchste Rolle.")
    if role.permissions.administrator and ctx.author.id not in OWNERS: return await clean(ctx,"Du kannst keine Admin-Rollen vergeben.")
    if role>=ctx.author.top_role and ctx.author.id not in OWNERS: return await clean(ctx,"Du kannst keine Rolle vergeben, die gleich oder höher als deine eigene ist.")
    try:
        if role in member.roles:
            await member.remove_roles(role,reason=f"?role von {ctx.author}")
            await ctx.send(f"**{role.name}** wurde von {member.mention} entfernt.")
        else:
            await member.add_roles(role,reason=f"?role von {ctx.author}")
            await ctx.send(f"**{role.name}** wurde {member.mention} zugewiesen.")
    except discord.Forbidden: await ctx.send("Keine Berechtigung für diese Rolle.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command(name="roleall")
async def roleall_cmd(ctx:commands.Context,role:discord.Role=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not role: return await clean(ctx,"Verwendung: `?roleall @role`")
    if role>=ctx.guild.me.top_role: return await clean(ctx,"Diese Rolle ist gleich oder höher als meine höchste Rolle.")
    if role.permissions.administrator and ctx.author.id not in OWNERS: return await clean(ctx,"Nur Owner können Admin-Rollen massenhaft vergeben.")
    msg=await ctx.send(f"Weise **{role.name}** allen Mitgliedern zu…"); count=0
    for m in ctx.guild.members:
        if m.bot or role in m.roles: continue
        try: await m.add_roles(role,reason=f"?roleall von {ctx.author}"); count+=1
        except: pass
        await asyncio.sleep(0.3)
    await msg.edit(content=f"Fertig. **{role.name}** wurde **{count}** Mitglied(ern) zugewiesen.")
    await mlog(ctx.guild,"Role All",f"{ctx.author} hat **{role.name}** an {count} Mitglieder vergeben.")

@bot.command(name="rolecreate")
async def rolecreate_cmd(ctx:commands.Context,role_name:str=None,color_hex:str="#000000",hoist:str="no"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not role_name: return await clean(ctx,"Verwendung: `?rolecreate <Name> [#Farbe] [yes/no]`")
    try:
        c=color_hex.lstrip("#"); color=discord.Color.from_rgb(int(c[0:2],16),int(c[2:4],16),int(c[4:6],16))
    except: color=discord.Color.default()
    view=RoleCreateView(ctx,role_name,color,hoist.lower() in("yes","ja","true","1"))
    await ctx.send(f"Berechtigungen für **{role_name}** auswählen:",view=view)

@bot.command(name="roleinfo")
async def roleinfo_cmd(ctx:commands.Context,*,role_input:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not role_input: return await clean(ctx,"Verwendung: `?roleinfo <Rollenname oder ID>`")
    matches=find_role(ctx.guild,role_input)
    if not matches: return await clean(ctx,"Keine Rolle gefunden.")
    r=matches[0]; perms=[p.replace("_"," ").title() for p,v in r.permissions if v]
    embed=discord.Embed(title=r.name,color=r.color if r.color.value else 0x2B2D31,timestamp=datetime.utcnow())
    embed.add_field(name="ID",value=r.id,inline=True); embed.add_field(name="Farbe",value=str(r.color),inline=True)
    embed.add_field(name="Mitglieder",value=str(len(r.members)),inline=True); embed.add_field(name="Erwähnbar",value="Ja" if r.mentionable else "Nein",inline=True)
    embed.add_field(name="Gehisst",value="Ja" if r.hoist else "Nein",inline=True); embed.add_field(name="Erstellt",value=f"<t:{int(r.created_at.timestamp())}:R>",inline=True)
    embed.add_field(name="Berechtigungen",value=", ".join(perms[:20]) if perms else "Keine",inline=False)
    await ctx.send(embed=embed)

# ================================================================
#  HACKBAN — PREFIX
# ================================================================

@bot.command()
async def hackban(ctx:commands.Context,member:discord.Member=None,*,reason:str="Kein Grund angegeben"):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not member: return await clean(ctx,"Verwendung: `?hackban @user [Grund]`")
    if member.id in OWNERS: return await clean(ctx,"Du kannst keinen Server-Owner hackbannen.")
    try:
        await ctx.guild.ban(member,reason=f"Hackban von {ctx.author}: {reason}",delete_message_days=1)
        _hb_add(ctx.guild.id,member.id,ctx.author.id,reason)
        await ctx.send(f"**{member}** wurde gehackbannt. | {reason}")
        await mlog(ctx.guild,"Hackban",f"{ctx.author} hat {member} ({member.id}) gehackbannt. Grund: {reason}")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command()
async def hackban_addalt(ctx:commands.Context,main_id:str=None,alt_id:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not main_id or not alt_id or not main_id.isdigit() or not alt_id.isdigit(): return await clean(ctx,"Verwendung: `?hackban_addalt <main_id> <alt_id>`")
    if not _hb_get(ctx.guild.id,int(main_id)): return await ctx.send("Kein Hackban für diese ID gefunden.")
    _hb_alt(ctx.guild.id,int(main_id),int(alt_id))
    try:
        await ctx.guild.ban(discord.Object(id=int(alt_id)),reason=f"Hackban Alt von {main_id}")
        await ctx.send(f"Alt `{alt_id}` wurde gebannt und mit `{main_id}` verknüpft.")
        await mlog(ctx.guild,"Hackban Alt",f"{ctx.author} hat Alt {alt_id} mit Hackban {main_id} verknüpft.")
    except Exception as e: await ctx.send(f"Alt verknüpft. Bann fehlgeschlagen: {e}")

@bot.command()
async def unhackban(ctx:commands.Context,user_id:str=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    if not user_id or not user_id.isdigit(): return await clean(ctx,"Verwendung: `?unhackban <user_id>`")
    uid=int(user_id); _hb_del(ctx.guild.id,uid)
    try:
        await ctx.guild.unban(discord.Object(id=uid),reason=f"Unhackban von {ctx.author}")
        await ctx.send(f"Hackban für `{user_id}` wurde aufgehoben.")
        await mlog(ctx.guild,"Unhackban",f"{ctx.author} hat Hackban für {user_id} aufgehoben.")
    except discord.NotFound: await ctx.send("Eintrag entfernt. Benutzer war nicht mehr gebannt.")
    except Exception as e: await ctx.send(f"Fehler: {e}")

@bot.command(name="unbanall")
async def unbanall_cmd(ctx:commands.Context):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not can_mod(ctx.author): return await clean(ctx,"Keine Berechtigung.")
    msg=await ctx.send("Alle Nutzer entbannen…"); count=0
    async for entry in ctx.guild.bans():
        try: await ctx.guild.unban(entry.user,reason=f"?unbanall von {ctx.author}"); count+=1
        except: pass
        await asyncio.sleep(0.3)
    await msg.edit(content=f"Fertig. **{count}** Nutzer entbannt.")
    await mlog(ctx.guild,"Unban All",f"{ctx.author} hat {count} Nutzer entbannt.")

@bot.command()
async def setcount(ctx:commands.Context,number:int=None):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    if not is_owner(ctx.author.id): return await clean(ctx,"Keine Berechtigung.")
    if number is None: return await clean(ctx,"Verwendung: `?setcount <Zahl>`")
    counting_state["current"]=number; counting_state["last_user"]=None
    _cnt_save(ctx.guild.id,number,0)
    await ctx.send(f"Zähler auf **{number}** gesetzt.",delete_after=5)
    try: await ctx.message.delete()
    except: pass

@bot.command()
async def call(ctx:commands.Context):
    if ctx.guild.id!=ALLOWED_GUILD_ID or not is_owner(ctx.author.id): return
    ch=bot.get_channel(CALL_VOICE_CHANNEL_ID)
    try:
        vc=ctx.voice_client
        if vc and vc.is_connected(): await vc.move_to(ch)
        else: await ch.connect()
        await ctx.send("Verbunden.",delete_after=3)
    except Exception as e: await ctx.send(f"Fehler: {e}")

# ================================================================
#  SLASH — PUBLIC
# ================================================================

@bot.tree.command(name="avatar",description="Avatar eines Mitglieds anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def avatar(interaction:discord.Interaction,member:discord.Member=None):
    m=member or interaction.user
    embed=discord.Embed(title=str(m),color=0x2B2D31); embed.set_image(url=m.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo",description="Server-Informationen anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def serverinfo(interaction:discord.Interaction):
    g=interaction.guild
    embed=discord.Embed(title=g.name,color=0x2B2D31,timestamp=datetime.utcnow())
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="ID",value=g.id,inline=True); embed.add_field(name="Owner",value=f"<@{g.owner_id}>",inline=True)
    embed.add_field(name="Erstellt",value=f"<t:{int(g.created_at.timestamp())}:R>",inline=True)
    embed.add_field(name="Mitglieder",value=g.member_count,inline=True); embed.add_field(name="Rollen",value=len(g.roles),inline=True)
    embed.add_field(name="Kanäle",value=len(g.channels),inline=True); embed.add_field(name="Boosts",value=g.premium_subscription_count,inline=True)
    embed.add_field(name="Boost-Level",value=g.premium_tier,inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleinfo",description="Informationen über eine Rolle anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_roleinfo(interaction:discord.Interaction,role:discord.Role):
    perms=[p.replace("_"," ").title() for p,v in role.permissions if v]
    embed=discord.Embed(title=role.name,color=role.color if role.color.value else 0x2B2D31,timestamp=datetime.utcnow())
    embed.add_field(name="ID",value=role.id,inline=True); embed.add_field(name="Farbe",value=str(role.color),inline=True)
    embed.add_field(name="Mitglieder",value=str(len(role.members)),inline=True); embed.add_field(name="Erwähnbar",value="Ja" if role.mentionable else "Nein",inline=True)
    embed.add_field(name="Gehisst",value="Ja" if role.hoist else "Nein",inline=True); embed.add_field(name="Erstellt",value=f"<t:{int(role.created_at.timestamp())}:R>",inline=True)
    embed.add_field(name="Berechtigungen",value=", ".join(perms[:20]) if perms else "Keine",inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="invite",description="Einladungsanzahl eines Mitglieds anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def invite_cmd(interaction:discord.Interaction,member:discord.Member):
    total,left,fake=_inv_get(interaction.guild.id,member.id); real=total-left-fake
    embed=discord.Embed(color=0x2B2D31,timestamp=datetime.utcnow())
    embed.set_author(name=f"Einladungen — {member.name}",icon_url=member.display_avatar.url)
    embed.description=f"{member.mention} hat **{real}** Einladung(en)"
    embed.add_field(name="Gesamt",value=total,inline=True); embed.add_field(name="Verlassen",value=left,inline=True); embed.add_field(name="Fake",value=fake,inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard",description="Einladungs-Leaderboard anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def leaderboard_cmd(interaction:discord.Interaction):
    top=_inv_top(interaction.guild.id)
    if not top: return await interaction.response.send_message("Noch keine Einladungsdaten vorhanden.",ephemeral=True)
    embed=discord.Embed(title="Einladungs-Leaderboard",color=0x2B2D31,timestamp=datetime.utcnow())
    medals={1:"",2:"",3:""}
    for i,(uid,total,left,fake) in enumerate(top,1):
        u=bot.get_user(uid); real=total-left-fake
        embed.add_field(name=f"{medals.get(i,f'{i}.')} {u.name if u else uid}",value=f"**{real}** Einladung(en) ({total} gesamt · {left} verlassen · {fake} fake)",inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="afk",description="AFK-Status setzen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def afk_cmd(interaction:discord.Interaction,reason:str="AFK"):
    afk_users[interaction.user.id]=(reason[:100],datetime.utcnow())
    await interaction.response.send_message(f"Dein AFK-Status wurde gesetzt: **{reason[:100]}**",ephemeral=True)

# ================================================================
#  SLASH — MODERATION
# ================================================================

@bot.tree.command(name="kick",description="Mitglied kicken.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_kick(interaction:discord.Interaction,member:discord.Member,reason:str="Kein Grund angegeben"):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if member.id in OWNERS or member.top_role>=interaction.guild.me.top_role: return await interaction.response.send_message("Diese Person kann nicht gekickt werden.",ephemeral=True)
    try:
        await member.kick(reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(f"**{member}** wurde von **{interaction.user.name}** gekickt. | {reason}")
        await mlog(interaction.guild,"Kick",f"{interaction.user} hat {member} ({member.id}) gekickt. Grund: {reason}")
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="ban",description="Mitglied bannen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_ban(interaction:discord.Interaction,member:discord.Member,reason:str="Kein Grund angegeben"):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if member.id in OWNERS or member.top_role>=interaction.guild.me.top_role: return await interaction.response.send_message("Diese Person kann nicht gebannt werden.",ephemeral=True)
    try:
        await member.ban(reason=f"{interaction.user}: {reason}",delete_message_days=1)
        await interaction.response.send_message(f"**{member}** wurde von **{interaction.user.name}** gebannt. | {reason}")
        await mlog(interaction.guild,"Ban",f"{interaction.user} hat {member} ({member.id}) gebannt. Grund: {reason}")
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="unban",description="Nutzer per ID entbannen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_unban(interaction:discord.Interaction,user_id:str,reason:str="Kein Grund angegeben"):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if not user_id.isdigit(): return await interaction.response.send_message("Bitte eine gültige Nutzer-ID angeben.",ephemeral=True)
    try:
        user=await bot.fetch_user(int(user_id)); await interaction.guild.unban(user,reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(f"**{user}** wurde entbannt.")
        await mlog(interaction.guild,"Unban",f"{interaction.user} hat {user} ({user.id}) entbannt.")
    except discord.NotFound: await interaction.response.send_message("Nutzer nicht gefunden oder nicht gebannt.",ephemeral=True)
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="timeout",description="Mitglied timeouten. Dauer: z.B. 10m, 2h, 1d",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_timeout(interaction:discord.Interaction,member:discord.Member,duration:str="10m",reason:str="Kein Grund angegeben"):
    if not can_timeout(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    secs=parse_time(duration)
    if secs is None: return await interaction.response.send_message("Ungültige Dauer. Beispiele: `10m`, `2h`, `1d`",ephemeral=True)
    if secs>2419200: return await interaction.response.send_message("Maximum ist 28 Tage.",ephemeral=True)
    try:
        until=discord.utils.utcnow()+timedelta(seconds=secs)
        await member.timeout(until,reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(f"**{member}** wurde für **{duration}** getimeouted. | {reason}")
        await mlog(interaction.guild,"Timeout",f"{interaction.user} hat {member} ({member.id}) für {duration} getimeouted.")
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="untimeout",description="Timeout eines Mitglieds aufheben.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_untimeout(interaction:discord.Interaction,member:discord.Member):
    if not can_timeout(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    try:
        await member.timeout(None,reason=f"Timeout aufgehoben von {interaction.user}")
        await interaction.response.send_message(f"Timeout für **{member}** wurde aufgehoben.")
        await mlog(interaction.guild,"Timeout Aufgehoben",f"{interaction.user} hat Timeout von {member} ({member.id}) aufgehoben.")
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="warn",description="Mitglied verwarnen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_warn(interaction:discord.Interaction,member:discord.Member,reason:str="Kein Grund angegeben"):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    wid=_warn_add(interaction.guild.id,member.id,interaction.user.id,reason); wl=_warn_get(interaction.guild.id,member.id)
    await interaction.response.send_message(f"**{member}** wurde verwarnt (#{wid}, gesamt: {len(wl)}). | {reason}")
    await mlog(interaction.guild,"Verwarnung",f"{interaction.user} hat {member} ({member.id}) verwarnt — #{wid}. {reason}")
    try:
        await member.send(embed=discord.Embed(title=f"Verwarnung — {interaction.guild.name}",
            description=f"**Grund:** {reason}\n**Verwarnung #{wid}** — Gesamt: {len(wl)}",color=discord.Color.yellow(),timestamp=datetime.utcnow()))
    except: pass

@bot.tree.command(name="warns",description="Verwarnungen eines Mitglieds anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_warns(interaction:discord.Interaction,member:discord.Member):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    wl=_warn_get(interaction.guild.id,member.id)
    embed=discord.Embed(title=f"Verwarnungen — {member}",color=discord.Color.yellow(),timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    if not wl: embed.description="Keine Verwarnungen."
    else:
        for wid,mid,r,ts in wl:
            m=interaction.guild.get_member(mid)
            embed.add_field(name=f"#{wid} — {ts[:10]}",value=f"**Grund:** {r}\n**Moderator:** {str(m) if m else mid}",inline=False)
    await interaction.response.send_message(embed=embed,ephemeral=True)

@bot.tree.command(name="purge",description="Nachrichten in diesem Kanal löschen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_purge(interaction:discord.Interaction,amount:int):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if amount<1 or amount>1000: return await interaction.response.send_message("Anzahl muss zwischen 1 und 1000 liegen.",ephemeral=True)
    await interaction.response.defer(ephemeral=True); deleted=await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"{len(deleted)} Nachricht(en) gelöscht.",ephemeral=True)

@bot.tree.command(name="slowmode",description="Slowmode für einen Kanal setzen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_slowmode(interaction:discord.Interaction,seconds:int,channel:discord.TextChannel=None):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if seconds<0 or seconds>21600: return await interaction.response.send_message("Wert muss zwischen 0 und 21600 liegen.",ephemeral=True)
    target=channel or interaction.channel
    try:
        await target.edit(slowmode_delay=seconds)
        msg=f"Slowmode in {target.mention} deaktiviert." if seconds==0 else f"Slowmode auf **{seconds}s** in {target.mention} gesetzt."
        await interaction.response.send_message(msg)
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

# ================================================================
#  SLASH — ROLES
# ================================================================

@bot.tree.command(name="role",description="Rolle einem Mitglied zuweisen oder entfernen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_role(interaction:discord.Interaction,member:discord.Member,role:discord.Role):
    if not can_role(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if role>=interaction.guild.me.top_role: return await interaction.response.send_message("Diese Rolle ist gleich oder höher als meine höchste Rolle.",ephemeral=True)
    if role.permissions.administrator and interaction.user.id not in OWNERS: return await interaction.response.send_message("Du kannst keine Admin-Rollen vergeben.",ephemeral=True)
    if role>=interaction.user.top_role and interaction.user.id not in OWNERS: return await interaction.response.send_message("Du kannst keine Rolle vergeben, die gleich oder höher als deine eigene ist.",ephemeral=True)
    try:
        if role in member.roles:
            await member.remove_roles(role,reason=f"/role von {interaction.user}")
            await interaction.response.send_message(f"**{role.name}** von {member.mention} entfernt.")
        else:
            await member.add_roles(role,reason=f"/role von {interaction.user}")
            await interaction.response.send_message(f"**{role.name}** {member.mention} zugewiesen.")
    except discord.Forbidden: await interaction.response.send_message("Keine Berechtigung für diese Rolle.",ephemeral=True)

@bot.tree.command(name="roleall",description="Rolle allen Mitgliedern zuweisen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_roleall(interaction:discord.Interaction,role:discord.Role):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if role>=interaction.guild.me.top_role: return await interaction.response.send_message("Diese Rolle ist gleich oder höher als meine höchste Rolle.",ephemeral=True)
    if role.permissions.administrator and interaction.user.id not in OWNERS: return await interaction.response.send_message("Nur Owner können Admin-Rollen massenhaft vergeben.",ephemeral=True)
    await interaction.response.defer(); count=0
    for m in interaction.guild.members:
        if m.bot or role in m.roles: continue
        try: await m.add_roles(role,reason=f"/roleall von {interaction.user}"); count+=1
        except: pass
        await asyncio.sleep(0.3)
    await interaction.followup.send(f"Fertig. **{role.name}** an **{count}** Mitglied(er) vergeben.")
    await mlog(interaction.guild,"Role All",f"{interaction.user} hat **{role.name}** an {count} Mitglieder vergeben.")

@bot.tree.command(name="hackban",description="Mitglied hackbannen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_hackban(interaction:discord.Interaction,member:discord.Member,reason:str="Kein Grund angegeben"):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if member.id in OWNERS: return await interaction.response.send_message("Du kannst keinen Server-Owner hackbannen.",ephemeral=True)
    try:
        await interaction.guild.ban(member,reason=f"Hackban von {interaction.user}: {reason}",delete_message_days=1)
        _hb_add(interaction.guild.id,member.id,interaction.user.id,reason)
        await interaction.response.send_message(f"**{member}** wurde gehackbannt. | {reason}")
        await mlog(interaction.guild,"Hackban",f"{interaction.user} hat {member} ({member.id}) gehackbannt. Grund: {reason}")
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="hackban_addalt",description="Alt-Account mit einem Hackban verknüpfen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_hackban_addalt(interaction:discord.Interaction,main_id:str,alt_id:str):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if not main_id.isdigit() or not alt_id.isdigit(): return await interaction.response.send_message("Bitte gültige IDs angeben.",ephemeral=True)
    if not _hb_get(interaction.guild.id,int(main_id)): return await interaction.response.send_message("Kein Hackban für diese ID.",ephemeral=True)
    _hb_alt(interaction.guild.id,int(main_id),int(alt_id))
    try:
        await interaction.guild.ban(discord.Object(id=int(alt_id)),reason=f"Hackban Alt von {main_id}")
        await interaction.response.send_message(f"Alt `{alt_id}` wurde gebannt und mit `{main_id}` verknüpft.")
        await mlog(interaction.guild,"Hackban Alt",f"{interaction.user} hat Alt {alt_id} mit Hackban {main_id} verknüpft.")
    except Exception as e: await interaction.response.send_message(f"Alt verknüpft. Bann fehlgeschlagen: {e}",ephemeral=True)

@bot.tree.command(name="unhackban",description="Hackban aufheben.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def slash_unhackban(interaction:discord.Interaction,user_id:str):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if not user_id.isdigit(): return await interaction.response.send_message("Bitte eine gültige Nutzer-ID angeben.",ephemeral=True)
    uid=int(user_id); _hb_del(interaction.guild.id,uid)
    try:
        await interaction.guild.unban(discord.Object(id=uid),reason=f"Unhackban von {interaction.user}")
        await interaction.response.send_message(f"Hackban für `{user_id}` wurde aufgehoben.")
        await mlog(interaction.guild,"Unhackban",f"{interaction.user} hat Hackban für {user_id} aufgehoben.")
    except discord.NotFound: await interaction.response.send_message("Eintrag entfernt. Nutzer war nicht mehr gebannt.",ephemeral=True)
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="invites_set",description="Einladungsanzahl eines Mitglieds manuell setzen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def invites_set(interaction:discord.Interaction,member:discord.Member,amount:int):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    _inv_set(interaction.guild.id,member.id,amount)
    await interaction.response.send_message(f"Einladungsanzahl für {member.mention} auf **{amount}** gesetzt.",ephemeral=True)

@bot.tree.command(name="alts",description="Accounts jünger als X Tage auflisten.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def alts_cmd(interaction:discord.Interaction,days:int=7):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if days<1 or days>365: return await interaction.response.send_message("Tage müssen zwischen 1 und 365 liegen.",ephemeral=True)
    now=datetime.utcnow()
    alts=[m for m in interaction.guild.members if not m.bot and (now-m.created_at.replace(tzinfo=None))<timedelta(days=days)]
    if not alts: return await interaction.response.send_message(f"Keine Accounts jünger als {days} Tag(e).",ephemeral=True)
    embed=discord.Embed(title=f"Accounts jünger als {days} Tag(e)",color=discord.Color.orange(),timestamp=datetime.utcnow())
    embed.set_footer(text=f"{len(alts)} Ergebnis(se)")
    lines=[f"{m.mention} — {(now-m.created_at.replace(tzinfo=None)).days}T (<t:{int(m.created_at.timestamp())}:R>)" for m in sorted(alts,key=lambda x:x.created_at,reverse=True)[:20]]
    embed.description="\n".join(lines)
    if len(alts)>20: embed.description+=f"\n*…und {len(alts)-20} weitere*"
    await interaction.response.send_message(embed=embed)

# ================================================================
#  SLASH — CHANNEL PERMISSIONS
# ================================================================

_CH_PERMS=["view_channel","send_messages","read_message_history","attach_files","embed_links",
    "add_reactions","use_external_emojis","mention_everyone","manage_messages","manage_channels",
    "connect","speak","stream","use_voice_activation","mute_members","deafen_members","move_members",
    "send_tts_messages","create_instant_invite"]

class PermSelect(discord.ui.Select):
    def __init__(self,channel,role):
        self.channel=channel; self.role=role
        super().__init__(placeholder="Berechtigung auswählen...",
            options=[discord.SelectOption(label=p.replace("_"," ").title(),value=p) for p in _CH_PERMS],custom_id="perm_sel")
    async def callback(self,interaction:discord.Interaction):
        perm=self.values[0]; view=PermValueView(self.channel,self.role,perm)
        embed=discord.Embed(description=f"**{perm.replace('_',' ').title()}** für **{self.role.name}** in **{self.channel.name}** setzen:",color=0x2B2D31)
        await interaction.response.edit_message(embed=embed,view=view)

class PermValueView(discord.ui.View):
    def __init__(self,channel,role,perm): super().__init__(timeout=60); self.channel=channel; self.role=role; self.perm=perm
    @discord.ui.button(label="Erlauben",style=discord.ButtonStyle.success)
    async def allow(self,i,b): await self._apply(i,True)
    @discord.ui.button(label="Verweigern",style=discord.ButtonStyle.danger)
    async def deny(self,i,b): await self._apply(i,False)
    @discord.ui.button(label="Neutral",style=discord.ButtonStyle.secondary)
    async def neutral(self,i,b): await self._apply(i,None)
    async def _apply(self,interaction,value):
        try:
            ow=self.channel.overwrites_for(self.role); setattr(ow,self.perm,value)
            await self.channel.set_permissions(self.role,overwrite=ow)
            label="Erlaubt" if value is True else ("Verweigert" if value is False else "Neutral")
            await interaction.response.edit_message(embed=discord.Embed(
                description=f"**{self.perm.replace('_',' ').title()}** wurde auf **{label}** für **{self.role.name}** in **{self.channel.name}** gesetzt.",
                color=0x2B2D31),view=None)
        except Exception as e:
            await interaction.response.edit_message(embed=discord.Embed(description=f"Fehler: {e}",color=discord.Color.red()),view=None)

@bot.tree.command(name="channel_perms",description="Berechtigungs-Override für eine Rolle in einem Kanal setzen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def channel_perms_cmd(interaction:discord.Interaction,channel:discord.abc.GuildChannel,role:discord.Role):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    view=discord.ui.View(timeout=60); view.add_item(PermSelect(channel,role))
    embed=discord.Embed(description=f"Berechtigung für **{role.name}** in **{channel.name}** konfigurieren:",color=0x2B2D31)
    await interaction.response.send_message(embed=embed,view=view,ephemeral=True)

# ================================================================
#  SLASH — SECURITY MODULES
# ================================================================

@bot.tree.command(name="enable",description="Security-Modul aktivieren.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def enable_cmd(interaction:discord.Interaction,module:str):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if module not in SECURITY_MODULES: return await interaction.response.send_message(f"Unbekanntes Modul. Verfügbar: {', '.join(f'`{m}`' for m in SECURITY_MODULES)}",ephemeral=True)
    _ssec(interaction.guild.id,module,True)
    await interaction.response.send_message(f"`{module}` wurde **aktiviert**.")
    await mlog(interaction.guild,"Modul Aktiviert",f"{interaction.user} hat `{module}` aktiviert.")

@bot.tree.command(name="disable",description="Security-Modul deaktivieren.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def disable_cmd(interaction:discord.Interaction,module:str):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if module not in SECURITY_MODULES: return await interaction.response.send_message(f"Unbekanntes Modul. Verfügbar: {', '.join(f'`{m}`' for m in SECURITY_MODULES)}",ephemeral=True)
    _ssec(interaction.guild.id,module,False)
    await interaction.response.send_message(f"`{module}` wurde **deaktiviert**.")
    await mlog(interaction.guild,"Modul Deaktiviert",f"{interaction.user} hat `{module}` deaktiviert.")

@enable_cmd.autocomplete("module")
@disable_cmd.autocomplete("module")
async def module_ac(interaction:discord.Interaction,current:str):
    return [discord.app_commands.Choice(name=m,value=m) for m in SECURITY_MODULES if current.lower() in m.lower()][:25]

@bot.tree.command(name="modules",description="Alle Security-Module und ihren Status anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def modules_cmd(interaction:discord.Interaction):
    if not can_mod(interaction.user): return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    lines=[f"{'`AN `' if _sec(interaction.guild.id,m) else '`AUS`'} {m}" for m in SECURITY_MODULES]
    embed=discord.Embed(title="Security-Module",description="\n".join(lines),color=0x2B2D31,timestamp=datetime.utcnow())
    await interaction.response.send_message(embed=embed,ephemeral=True)

# ================================================================
#  SLASH — CONFIG ID / MESSAGE
# ================================================================

@bot.tree.command(name="config_id",description="Kanal- oder Rollen-ID direkt aktualisieren.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def config_id_cmd(interaction:discord.Interaction,setting:str,new_id:str):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    setting=setting.upper().strip()
    if setting not in _ID_DEF: return await interaction.response.send_message(f"Unbekannte Einstellung. Verfügbar:\n{chr(10).join(f'`{k}`' for k in _ID_DEF)}",ephemeral=True)
    if not new_id.strip().isdigit(): return await interaction.response.send_message("Bitte eine gültige Discord-ID angeben.",ephemeral=True)
    _sid(interaction.guild.id,setting,int(new_id))
    await interaction.response.send_message(f"`{setting}` aktualisiert auf `{new_id}`.",ephemeral=True)
    await mlog(interaction.guild,"Config",f"{interaction.user} hat `{setting}` auf `{new_id}` gesetzt.")

@config_id_cmd.autocomplete("setting")
async def config_id_ac(interaction:discord.Interaction,current:str):
    return [discord.app_commands.Choice(name=k,value=k) for k in _ID_DEF if current.lower() in k.lower()][:25]

@bot.tree.command(name="bot_edit",description="Bot-Benutzername oder Avatar ändern.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def bot_edit_cmd(interaction:discord.Interaction,name:str=None,avatar_url:str=None):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if not name and not avatar_url: return await interaction.response.send_message("Bitte `name` und/oder `avatar_url` angeben.",ephemeral=True)
    await interaction.response.defer(ephemeral=True); kw={}
    if name: kw["username"]=name
    if avatar_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(avatar_url) as r:
                    if r.status==200: kw["avatar"]=await r.read()
                    else: return await interaction.followup.send(f"Avatar konnte nicht geladen werden (HTTP {r.status}).",ephemeral=True)
        except ImportError: return await interaction.followup.send("aiohttp ist nicht installiert.",ephemeral=True)
    try:
        await bot.user.edit(**kw)
        parts=[]
        if name: parts.append(f"Benutzername auf **{name}** geändert")
        if avatar_url: parts.append("Avatar aktualisiert")
        await interaction.followup.send(" | ".join(parts),ephemeral=True)
    except Exception as e: await interaction.followup.send(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="send",description="Nachricht als Bot senden (Embed, Bild, Link-Button).",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def send_cmd(interaction:discord.Interaction,channel:discord.TextChannel,message:str,
    embed:bool=True,color:str="black",image:bool=False,image_url:str=None,
    link_button:bool=False,link_url:str=None,link_label:str="Öffnen"):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if image and not image_url: return await interaction.response.send_message("Bitte `image_url` angeben wenn `image` aktiviert ist.",ephemeral=True)
    if link_button and not link_url: return await interaction.response.send_message("Bitte `link_url` angeben wenn `link_button` aktiviert ist.",ephemeral=True)
    view=discord.utils.MISSING
    if link_button and link_url:
        v=discord.ui.View(); v.add_item(discord.ui.Button(label=link_label,url=link_url,style=discord.ButtonStyle.link)); view=v
    try:
        if embed:
            emb=discord.Embed(description=message,color=parse_color(color))
            if image and image_url: emb.set_image(url=image_url)
            await channel.send(embed=emb,view=view if view is not discord.utils.MISSING else discord.utils.MISSING)
        else:
            content=f"{message}\n{image_url}" if image and image_url else message
            await channel.send(content,view=view if view is not discord.utils.MISSING else discord.utils.MISSING)
        await interaction.response.send_message("Nachricht gesendet.",ephemeral=True)
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="say",description="Nachricht als Bot senden (Nur Text).",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def say_cmd(interaction:discord.Interaction,channel:discord.TextChannel,text:str):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    try:
        await channel.send(text); await interaction.response.send_message("Nachricht gesendet.",ephemeral=True)
    except discord.Forbidden: await interaction.response.send_message("Keine Berechtigung in diesem Kanal.",ephemeral=True)
    except Exception as e: await interaction.response.send_message(f"Fehler: {e}",ephemeral=True)

@bot.tree.command(name="whitelist_add",description="Mitglied zur Security-Whitelist hinzufügen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def wl_add(interaction:discord.Interaction,member:discord.Member):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    SECURITY_WHITELIST_USERS.add(member.id)
    await interaction.response.send_message(f"{member.mention} wurde zur Whitelist hinzugefügt.",ephemeral=True)

@bot.tree.command(name="whitelist_remove",description="Mitglied von der Security-Whitelist entfernen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def wl_remove(interaction:discord.Interaction,member:discord.Member):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    SECURITY_WHITELIST_USERS.discard(member.id)
    await interaction.response.send_message(f"{member.mention} wurde von der Whitelist entfernt.",ephemeral=True)

@bot.tree.command(name="whitelist_list",description="Alle Mitglieder auf der Whitelist anzeigen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def wl_list(interaction:discord.Interaction):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    if not SECURITY_WHITELIST_USERS: return await interaction.response.send_message("Die Whitelist ist leer.",ephemeral=True)
    names=[f"{bot.get_user(uid)} (`{uid}`)" if bot.get_user(uid) else f"Unbekannt (`{uid}`)" for uid in SECURITY_WHITELIST_USERS]
    await interaction.response.send_message("**Security-Whitelist:**\n"+"\n".join(names),ephemeral=True)

# ================================================================
#  SECURITY EVENTS
# ================================================================

@bot.event
async def on_guild_channel_delete(channel:discord.abc.GuildChannel):
    if channel.guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(channel.guild.id,"anti_channel_delete"): return
    if getattr(channel,"category_id",None)==_cid(channel.guild.id,"TICKET_CATEGORY_ID") and channel.name.startswith("ticket-"): return
    saved={"name":channel.name,"type":channel.type,"category":channel.category,"position":channel.position,
           "overwrites":channel.overwrites,"topic":getattr(channel,"topic",None),"nsfw":getattr(channel,"nsfw",False),"slowmode":getattr(channel,"slowmode_delay",0)}
    await asyncio.sleep(0.3)
    e=await audit(channel.guild,discord.AuditLogAction.channel_delete)
    if not e: return
    user=e.user
    if not user or whitelisted(channel.guild.get_member(user.id) or user): return
    m = channel.guild.get_member(user.id)
    await _execute_punishment(channel.guild, m or user, "channel_del", f"Kanal gelöscht: #{saved['name']}")
    await mlog(channel.guild,"Auto-Strafe (Kanal-Löschung)",f"{user} ({user.id}) hat #{saved['name']} gelöscht.")
    try:
        kw=dict(name=saved["name"],overwrites=saved["overwrites"],category=saved["category"],position=saved["position"],reason="Auto-Wiederherstellung")
        if saved["type"]==discord.ChannelType.text:
            if saved["topic"]: kw["topic"]=saved["topic"]
            kw["nsfw"]=saved["nsfw"]; kw["slowmode_delay"]=saved["slowmode"]
            await channel.guild.create_text_channel(**kw)
        elif saved["type"]==discord.ChannelType.voice: await channel.guild.create_voice_channel(**kw)
        else: await channel.guild.create_text_channel(**kw)
    except: pass

@bot.event
async def on_guild_channel_create(channel:discord.abc.GuildChannel):
    if channel.guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(channel.guild.id,"anti_channel_create"): return
    await asyncio.sleep(0.3)
    e=await audit(channel.guild,discord.AuditLogAction.channel_create)
    if not e: return
    user=e.user
    if not user or whitelisted(channel.guild.get_member(user.id) or user): return
    now=datetime.utcnow()
    channel_create_tracker[user.id]=[t for t in channel_create_tracker[user.id] if now-t<timedelta(seconds=CREATE_WIN)]
    channel_create_tracker[user.id].append(now)
    if len(channel_create_tracker[user.id])>=CREATE_MAX:
        m = channel.guild.get_member(user.id)
        await _execute_punishment(channel.guild, m or user, "channel_spam", f"Channel-Spam ({CREATE_MAX}+ in {CREATE_WIN}s)")
        await mlog(channel.guild,"Auto-Strafe (Channel-Spam)",f"{user} ({user.id}) hat {len(channel_create_tracker[user.id])} Kanäle in {CREATE_WIN}s erstellt.")
        channel_create_tracker[user.id].clear()

@bot.event
async def on_guild_role_delete(role:discord.Role):
    if role.guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(role.guild.id,"anti_role_delete"): return
    saved={"name":role.name,"color":role.color,"permissions":role.permissions,"hoist":role.hoist,"mentionable":role.mentionable}
    await asyncio.sleep(0.3)
    e=await audit(role.guild,discord.AuditLogAction.role_delete)
    if not e: return
    user=e.user
    if not user or whitelisted(role.guild.get_member(user.id) or user): return
    m = role.guild.get_member(user.id)
    await _execute_punishment(role.guild, m or user, "role_del", f"Rolle gelöscht: {saved['name']}")
    await mlog(role.guild,"Auto-Strafe (Rollen-Löschung)",f"{user} ({user.id}) hat Rolle **{saved['name']}** gelöscht.")
    try:
        await role.guild.create_role(name=saved["name"],color=saved["color"],permissions=saved["permissions"],hoist=saved["hoist"],mentionable=saved["mentionable"],reason="Auto-Wiederherstellung")
    except: pass

@bot.event
async def on_guild_role_create(role:discord.Role):
    if role.guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(role.guild.id,"anti_role_create"): return
    await asyncio.sleep(0.3)
    e=await audit(role.guild,discord.AuditLogAction.role_create)
    if not e: return
    user=e.user
    if not user or whitelisted(role.guild.get_member(user.id) or user): return
    now=datetime.utcnow()
    role_create_tracker[user.id]=[t for t in role_create_tracker[user.id] if now-t<timedelta(seconds=CREATE_WIN)]
    role_create_tracker[user.id].append(now)
    if len(role_create_tracker[user.id])>=CREATE_MAX:
        m = role.guild.get_member(user.id)
        await _execute_punishment(role.guild, m or user, "role_spam", f"Rollen-Spam ({CREATE_MAX}+ in {CREATE_WIN}s)")
        await mlog(role.guild,"Auto-Strafe (Rollen-Spam)",f"{user} ({user.id}) hat {len(role_create_tracker[user.id])} Rollen in {CREATE_WIN}s erstellt.")
        role_create_tracker[user.id].clear()

@bot.event
async def on_webhooks_update(channel:discord.TextChannel):
    if channel.guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(channel.guild.id,"anti_webhook"): return
    await asyncio.sleep(0.3)
    e=await audit(channel.guild,discord.AuditLogAction.webhook_create)
    if not e: return
    user=e.user
    if not user or whitelisted(channel.guild.get_member(user.id) or user): return
    try:
        for w in await channel.webhooks(): await w.delete()
    except: pass
    m = channel.guild.get_member(user.id)
    await _execute_punishment(channel.guild, m or user, "webhook", "Webhook-Angriff erkannt")
    await mlog(channel.guild,"Auto-Strafe (Webhook)",f"{user} ({user.id}) hat einen Webhook erstellt.")

@bot.event
async def on_member_ban(guild:discord.Guild,user:discord.User):
    if guild.id!=ALLOWED_GUILD_ID: return
    if not _sec(guild.id,"anti_mass_ban"): return
    await asyncio.sleep(0.3)
    e=await audit(guild,discord.AuditLogAction.ban)
    if not e: return
    actor=e.user
    if not actor or whitelisted(guild.get_member(actor.id) or actor): return
    now=datetime.utcnow()
    ban_tracker[actor.id]=[t for t in ban_tracker[actor.id] if now-t<timedelta(seconds=20)]
    ban_tracker[actor.id].append(now)
    if len(ban_tracker[actor.id])>=2:
        m = guild.get_member(actor.id)
        await _execute_punishment(guild, m or actor, "mass_ban", "Mass Ban (2+ in 20s)")
        await mlog(guild,"Auto-Strafe (Mass Ban)",f"{actor} ({actor.id}) hat 2+ Mitglieder in 20s gebannt.")

@bot.event
async def on_audit_log_entry_create(entry:discord.AuditLogEntry):
    if entry.guild.id!=ALLOWED_GUILD_ID: return

    if entry.action==discord.AuditLogAction.member_update and _sec(entry.guild.id,"anti_mass_timeout"):
        actor=entry.user
        if not actor or whitelisted(entry.guild.get_member(actor.id) or actor): return
        ch=entry.changes; after={c.key:c.new for c in ch.after} if hasattr(ch,"after") else {}
        if "timed_out_until" in after and after["timed_out_until"] is not None:
            now=datetime.utcnow()
            timeout_tracker[actor.id]=[t for t in timeout_tracker[actor.id] if now-t<timedelta(seconds=15)]
            timeout_tracker[actor.id].append(now)
            if len(timeout_tracker[actor.id])>=2:
                m = entry.guild.get_member(actor.id)
                await _execute_punishment(entry.guild, m or actor, "mass_timeout", "Mass Timeout (2+ in 15s)")
                await mlog(entry.guild,"Auto-Strafe (Mass Timeout)",f"{actor} ({actor.id}) hat 2+ Mitglieder in 15s getimeouted.")

    if entry.action==discord.AuditLogAction.role_update and _sec(entry.guild.id,"anti_admin_perm"):
        actor=entry.user
        if not actor or whitelisted(entry.guild.get_member(actor.id) or actor): return
        role=entry.target
        if not role or role.position>=entry.guild.me.top_role.position: return
        ap=None
        if hasattr(entry.changes,"after"):
            for c in entry.changes.after:
                if c.key=="permissions": ap=c.new; break
        if ap and ap.administrator:
            try:
                p=discord.Permissions(ap.value); p.administrator=False
                await role.edit(permissions=p,reason="Admin-Berechtigung blockiert")
            except: pass
            m=entry.guild.get_member(actor.id)
            if m:
                await _execute_punishment(entry.guild, m, "admin_perm", "Admin-Berechtigung vergeben")
                await mlog(entry.guild,"Admin-Berechtigung Blockiert",f"{actor} ({actor.id}) hat versucht, Admin-Berechtigung an **{role.name}** zu vergeben.")

    if entry.action==discord.AuditLogAction.guild_update:
        actor=entry.user
        if actor and not actor.bot:
            await mlog(entry.guild,"Server Aktualisiert",f"{actor} ({actor.id}) hat Server-Einstellungen geändert.")

    if entry.action==discord.AuditLogAction.bot_add:
        actor=entry.user; ba=entry.target
        await mlog(entry.guild,"Bot Hinzugefügt",f"{actor} ({actor.id}) hat Bot {ba} ({getattr(ba,'id','?')}) hinzugefügt.")

# ================================================================
#  HELP
# ================================================================

@bot.command(name="help")
async def help_cmd(ctx:commands.Context):
    if ctx.guild.id!=ALLOWED_GUILD_ID: return
    embed=discord.Embed(title="Befehlsreferenz",color=0x2B2D31,timestamp=datetime.utcnow())
    embed.add_field(name="Moderation",value=(
        "`?kick` `?ban` `?unban` `?unbanall`\n"
        "`?timeout` `?rto` `?purge` `?slowmode`\n"
        "`?warn` `?warns` `?clearwarn` `?clearwarns`\n"
        "Alle auch als Slash-Commands verfügbar."),inline=False)
    embed.add_field(name="Rollen",value=(
        "`?role @user <Name/ID>` — Rolle togglen\n"
        "`?roleall @role` — Allen zuweisen\n"
        "`?rolecreate <Name>` — Mit Berechtigungen erstellen\n"
        "`?roleinfo <Name>` | Auch `/role` `/roleall`"),inline=False)
    embed.add_field(name="Hackban",value=(
        "`?hackban @user` | `?hackban_addalt <ID> <altID>`\n"
        "`?unhackban <ID>` | Auch als Slash-Commands."),inline=False)
    embed.add_field(name="Tickets",value=(
        "`?close` `?delete`\n"
        "`/adduser` `/removeuser` `/renameticket`\n"
        "`/setup` — Ticket-System konfigurieren"),inline=False)
    embed.add_field(name="Night Mode",value=(
        "`/nightmode` — Manuell steuern oder Rollen konfigurieren\n"
        " 22:00 Uhr: Rollen-Perms entfernt\n"
        " 09:00 Uhr: Rollen-Perms wiederhergestellt\n"
        "Perms werden **beim Hinzufügen** gespeichert."),inline=False)
    embed.add_field(name="Security",value=(
        "`/enable <modul>` `/disable <modul>` `/modules`\n"
        "`/security_config` — Strafe für jeden Event einzeln konfigurieren\n"
        "Strafen: `none`, `clear_roles`, `timeout`, `kick`, `ban`"),inline=False)
    embed.add_field(name="Konfiguration",value=(
        "`/setup` — Ticket-System\n"
        "`/config` — Kanäle, Rollen und Nachrichten\n"
        "`/config_id` `/bot_edit` `/send` `/say`\n"
        "`/whitelist_add|remove|list`"),inline=False)
    embed.add_field(name="Öffentlich",value=(
        "`/avatar` `/serverinfo` `/roleinfo`\n"
        "`/invite` `/leaderboard` `/afk` `/alts`"),inline=False)
    embed.set_footer(text=f"Angefragt von {ctx.author}")
    await ctx.send(embed=embed)

# ================================================================
#  BOT ADD SECURITY
# ================================================================

async def _on_audit_bot_add_security(entry: discord.AuditLogEntry):
    if entry.guild.id != ALLOWED_GUILD_ID: return
    if entry.action != discord.AuditLogAction.bot_add: return

    added_bot = entry.target
    actor     = entry.user

    if added_bot:
        member = entry.guild.get_member(added_bot.id)
        if member:
            try:
                await member.kick(reason="Unerlaubte Bot-Hinzufügung — Auto-Schutz")
            except: pass

    if actor and not whitelisted(actor) and actor.id not in OWNERS:
        m = entry.guild.get_member(actor.id)
        if m and bot_can_act(entry.guild, m):
            await _execute_punishment(entry.guild, m, "bot_add", "Unerlaubten Bot hinzugefügt")
            await mlog(entry.guild, "Auto-Strafe (Bot hinzugefügt)",
                f"{actor} ({actor.id}) hat Bot {added_bot} "
                f"({getattr(added_bot, 'id', '?')}) hinzugefügt — Bot entfernt.")

bot.add_listener(_on_audit_bot_add_security, "on_audit_log_entry_create")

# ================================================================
#  MASS PING — clear roles + block (NO auto-ban by default)
# ================================================================

async def _on_message_mass_ping(message: discord.Message):
    if message.author.bot: return
    if not message.guild or message.guild.id != ALLOWED_GUILD_ID: return
    if not _sec(message.guild.id, "anti_mass_ping"): return
    if whitelisted(message.author): return
    if not ("@everyone" in message.content or "@here" in message.content): return

    now = datetime.utcnow()
    uid = message.author.id

    # Track this ping, clean up expired entries
    mass_ping_tracker[uid].append((now, message))
    mass_ping_tracker[uid] = [
        (t, msg) for t, msg in mass_ping_tracker[uid]
        if now - t < timedelta(seconds=PING_WINDOW)]

    count = len(mass_ping_tracker[uid])

    # Under threshold: do nothing, message goes through normally
    if count < PING_MAX:
        return

    # Threshold reached (2+ pings in PING_WINDOW seconds):
    # delete ALL tracked messages from this window, then punish once
    for _t, tracked_msg in list(mass_ping_tracker[uid]):
        try:
            await tracked_msg.delete()
        except: pass

    # Reset tracker so the next window starts fresh after punishment
    mass_ping_tracker[uid].clear()

    member = message.guild.get_member(uid)
    if member and bot_can_act(message.guild, member):
        await _execute_punishment(message.guild, member, "mass_ping",
            f"Mass-Ping ({count}x @everyone/@here innerhalb von {PING_WINDOW}s)")
        punishment = _sec_punishment_get(message.guild.id, "mass_ping")
        await mlog(message.guild, "Auto-Strafe (Mass Ping)",
            f"{member} ({member.id}) hat **{count}x** in {PING_WINDOW}s "
            f"@everyone/@here gesendet — Strafe: `{punishment}` | {count} Nachrichten gelöscht.")

bot.add_listener(_on_message_mass_ping, "on_message")

# ================================================================
#  /role_for_all_channels
# ================================================================

_PERM_FLAGS_READABLE = [
    "view_channel", "send_messages", "read_message_history",
    "attach_files", "embed_links", "add_reactions", "use_external_emojis",
    "mention_everyone", "manage_messages", "manage_channels",
    "connect", "speak", "stream", "use_voice_activation",
    "mute_members", "deafen_members", "move_members",
    "send_tts_messages", "create_instant_invite",
]

class RoleAllChannelsPermSelect(discord.ui.Select):
    def __init__(self):
        options=[discord.SelectOption(label=p.replace("_"," ").title(),value=p) for p in _PERM_FLAGS_READABLE]
        super().__init__(placeholder="Berechtigungen ERLAUBEN...",options=options,min_values=0,max_values=len(options),custom_id="rac_allow_sel")
    async def callback(self,interaction:discord.Interaction):
        self.view.allowed_perms=set(interaction.data["values"]); await interaction.response.defer()

class RoleAllChannelsDenySelect(discord.ui.Select):
    def __init__(self):
        options=[discord.SelectOption(label=p.replace("_"," ").title(),value=p) for p in _PERM_FLAGS_READABLE]
        super().__init__(placeholder="Berechtigungen VERWEIGERN...",options=options,min_values=0,max_values=len(options),custom_id="rac_deny_sel")
    async def callback(self,interaction:discord.Interaction):
        self.view.denied_perms=set(interaction.data["values"]); await interaction.response.defer()

class RoleAllChannelsView(discord.ui.View):
    def __init__(self,role:discord.Role):
        super().__init__(timeout=180); self.role=role; self.allowed_perms:set[str]=set(); self.denied_perms:set[str]=set()
        self.add_item(RoleAllChannelsPermSelect()); self.add_item(RoleAllChannelsDenySelect())
    @discord.ui.button(label="Auf alle Kanäle anwenden",style=discord.ButtonStyle.success,row=2)
    async def apply(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        count=0; errors=0; guild=interaction.guild; role=self.role
        for ch in guild.channels:
            ow=ch.overwrites_for(role)
            for perm in self.allowed_perms:
                if hasattr(ow,perm): setattr(ow,perm,True)
            for perm in self.denied_perms:
                if hasattr(ow,perm): setattr(ow,perm,False)
            try:
                await ch.set_permissions(role,overwrite=ow,reason=f"/role_for_all_channels von {interaction.user}"); count+=1
            except: errors+=1
            await asyncio.sleep(0.3)
        summary_allow=", ".join(self.allowed_perms) or "keine"; summary_deny=", ".join(self.denied_perms) or "keine"
        await interaction.followup.send(
            f"Fertig. **{role.name}** in **{count}** Kanal/-Kanälen aktualisiert (Fehler: {errors}).\n"
            f"Erlaubt: `{summary_allow}`\nVerweigert: `{summary_deny}`",ephemeral=True)
        await mlog(guild,"Rolle für alle Kanäle",f"{interaction.user} hat **{role.name}** in {count} Kanälen angepasst. Erlaubt: {summary_allow} | Verweigert: {summary_deny}")
    @discord.ui.button(label="Abbrechen",style=discord.ButtonStyle.danger,row=2)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button): await interaction.response.edit_message(content="Abgebrochen.",view=None)

@bot.tree.command(name="role_for_all_channels",description="Benutzerdefinierte Berechtigungs-Overrides für eine Rolle in allen Kanälen setzen.",guild=discord.Object(id=ALLOWED_GUILD_ID))
async def role_for_all_channels(interaction:discord.Interaction,role:discord.Role):
    if interaction.user.id not in OWNERS: return await interaction.response.send_message("Keine Berechtigung.",ephemeral=True)
    embed=discord.Embed(title="Rolle — Alle Kanäle",
        description=(f"**{role.name}** in allen Kanälen konfigurieren.\n\n"
            "1. Berechtigungen zum **Erlauben** auswählen.\n2. Berechtigungen zum **Verweigern** auswählen.\n3. Auf **Auf alle Kanäle anwenden** klicken."),
        color=0x2B2D31)
    view=RoleAllChannelsView(role)
    await interaction.response.send_message(embed=embed,view=view,ephemeral=True)

# ================================================================
#  /messages — Alle Bot-Nachrichten konfigurieren
# ================================================================

# Metadata for every configurable message
_ALL_MESSAGES = [
    {
        "key": "WELCOME_MSG",
        "label": "Willkommensnachricht",
        "desc": "Gesendet wenn ein neues Mitglied beitritt.",
        "vars": "{mention}  {rules}",
        "channel_key": "WELCOME_CHANNEL_ID",
        "example": "Hey {mention}, willkommen! Lies die Regeln: <#{rules}>",
    },
    {
        "key": "BOOST_MSG",
        "label": "Boost-Nachricht",
        "desc": "Gesendet nach 5 Sekunden wenn jemand boosted.",
        "vars": "Keine Variablen",
        "channel_key": "BOOST_CHANNEL_ID",
        "example": "thank you ",
    },
    {
        "key": "TICKET_PANEL_DESC",
        "label": "Ticket-Panel Text",
        "desc": "Text im Ticket-Panel Embed.",
        "vars": "Keine Variablen",
        "channel_key": "TICKET_PANEL_CHANNEL_ID",
        "example": "Klicke den Button um ein Ticket zu öffnen.",
    },
    {
        "key": "TICKET_OPEN_MSG",
        "label": "Ticket-Öffnungsnachricht",
        "desc": "Wird im Ticket gesendet sobald es geöffnet wird.",
        "vars": "Keine Variablen",
        "channel_key": "TICKET_CATEGORY_ID",
        "example": "Bitte beschreibe dein Anliegen.",
    },
    {
        "key": "TICKET_CLOSE_MSG",
        "label": "Ticket-Schließen Nachricht",
        "desc": "Wird gesendet wenn ein Ticket geschlossen wird.",
        "vars": "Keine Variablen",
        "channel_key": "TICKET_CATEGORY_ID",
        "example": "Dieses Ticket wurde geschlossen.",
    },
    {
        "key": "TICKET_DELETE_MSG",
        "label": "Ticket-Löschen Nachricht",
        "desc": "Wird kurz vor dem Löschen eines Tickets gesendet.",
        "vars": "Keine Variablen",
        "channel_key": "TICKET_CATEGORY_ID",
        "example": "Dieses Ticket wird in 3 Sekunden gelöscht.",
    },
    {
        "key": "INVITE_VANITY_MSG",
        "label": "Invite-Tracking: Vanity-Link",
        "desc": "Gesendet wenn jemand über den Vanity-Link beitritt.",
        "vars": "{mention}  {member}",
        "channel_key": "INVITE_CHANNEL_ID",
        "example": "{mention} ist über den Vanity-Link beigetreten.",
    },
    {
        "key": "INVITE_KNOWN_MSG",
        "label": "Invite-Tracking: Bekannter Einlader",
        "desc": "Gesendet wenn der Einlader bekannt ist.",
        "vars": "{mention}  {member}  {inviter}  {real}",
        "channel_key": "INVITE_CHANNEL_ID",
        "example": "{mention} beigetreten. Eingeladen von **{inviter}** — {real} Einladungen.",
    },
    {
        "key": "INVITE_UNKNOWN_MSG",
        "label": "Invite-Tracking: Unbekannter Einlader",
        "desc": "Gesendet wenn der Einlader unbekannt ist.",
        "vars": "{mention}  {member}",
        "channel_key": "INVITE_CHANNEL_ID",
        "example": "{mention} ist beigetreten. Einladender unbekannt.",
    },
    {
        "key": "FIRST_REACT_MSG",
        "label": "Erste Reaktion (Activity-Check)",
        "desc": "Gesendet wer als erstes auf eine Activity-Check Nachricht reagiert.",
        "vars": "{mention}  {user}",
        "channel_key": "ACTIVITY_CHECK_CHANNEL_ID",
        "example": "{mention} war der Erste! 🏆",
    },
]

# ---- helpers ----
def _get_channel_for_msg(guild, cfg):
    ckey = cfg.get("channel_key","")
    if ckey == "ACTIVITY_CHECK_CHANNEL_ID":
        ch = guild.get_channel(ACTIVITY_CHECK_CHANNEL_ID)
    else:
        ch = guild.get_channel(_cid(guild.id, ckey))
    return ch


class MessagesMainView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        options = []
        for m in _ALL_MESSAGES:
            options.append(discord.SelectOption(
                label=m["label"][:100],
                value=m["key"],
                description=m["desc"][:100]
            ))
        sel = discord.ui.Select(
            placeholder="Nachricht auswählen...",
            options=options,
            min_values=1, max_values=1,
            custom_id="messages_sel"
        )
        sel.callback = self.select_cb
        self.add_item(sel)

    async def select_cb(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        cfg = next((m for m in _ALL_MESSAGES if m["key"] == key), None)
        if not cfg:
            return await interaction.response.send_message("Unbekannte Nachricht.", ephemeral=True)
        await _show_message_detail(interaction, cfg)


async def _show_message_detail(interaction: discord.Interaction, cfg: dict):
    current = _cmsg(interaction.guild.id, cfg["key"])
    default = _MSG_DEF.get(cfg["key"], "")
    ch = _get_channel_for_msg(interaction.guild, cfg)
    ch_str = ch.mention if ch else " Kein Kanal konfiguriert"

    embed = discord.Embed(title=" " + cfg["label"], color=0x2B2D31)
    embed.add_field(name=" Kanal", value=ch_str, inline=True)
    embed.add_field(name=" Variablen", value="`" + cfg["vars"] + "`" if cfg["vars"] != "Keine Variablen" else "Keine", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    cur_preview = (current[:400] + "…") if current and len(current) > 400 else (current or "*Nicht gesetzt — Standard wird verwendet*")
    embed.add_field(name=" Aktueller Text", value="```" + cur_preview + "```", inline=False)

    def_preview = (default[:200] + "…") if default and len(default) > 200 else (default or "—")
    embed.add_field(name=" Standard-Text", value="```" + def_preview + "```", inline=False)
    embed.set_footer(text=" Bearbeiten |  Vorschau |  Zurücksetzen | ← Zurück")

    view = MessageDetailView(interaction.guild.id, cfg)
    await interaction.response.edit_message(embed=embed, view=view)


class MessageDetailView(discord.ui.View):
    def __init__(self, guild_id: int, cfg: dict):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.cfg = cfg

    @discord.ui.button(label=" Bearbeiten", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = _cmsg(interaction.guild.id, self.cfg["key"])
        await interaction.response.send_modal(
            MessageEditModal(self.guild_id, self.cfg, current)
        )

    @discord.ui.button(label=" Vorschau", style=discord.ButtonStyle.secondary, row=0)
    async def preview_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = _cmsg(interaction.guild.id, self.cfg["key"])
        if not current:
            return await interaction.response.send_message("Kein Text gesetzt.", ephemeral=True)
        try:
            rendered = current.format(
                mention=interaction.user.mention,
                member=str(interaction.user),
                user=str(interaction.user),
                rules=_cid(interaction.guild.id, "RULES_CHANNEL_ID") or "0",
                inviter="TestUser",
                real=42,
            )
        except Exception as e:
            rendered = current + f"\n\n Fehler beim Rendern: {e}"
        embed = discord.Embed(
            title=" Vorschau — " + self.cfg["label"],
            description=rendered,
            color=0x2B2D31
        )
        embed.set_footer(text="Dies ist nur eine Vorschau — keine echte Nachricht wurde gesendet.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label=" Standard", style=discord.ButtonStyle.danger, row=0)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        default = _MSG_DEF.get(self.cfg["key"], "")
        if not default:
            return await interaction.response.send_message("Kein Standard vorhanden.", ephemeral=True)
        _smsg(interaction.guild.id, self.cfg["key"], default)
        await mlog(interaction.guild, "Nachrichten Config",
            str(interaction.user) + " hat **" + self.cfg["label"] + "** zurückgesetzt.")
        await _return_to_messages(interaction,
            " **" + self.cfg["label"] + "** wurde auf den Standard zurückgesetzt.")

    @discord.ui.button(label="← Zurück", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _return_to_messages(interaction)


class MessageEditModal(discord.ui.Modal):
    def __init__(self, guild_id: int, cfg: dict, current: str):
        super().__init__(title="Bearbeiten — " + cfg["label"][:40])
        self.guild_id = guild_id
        self.cfg = cfg
        ph = "Variablen: " + cfg["vars"] if cfg["vars"] != "Keine Variablen" else "Text eingeben..."
        self.field = discord.ui.TextInput(
            label=cfg["label"][:45],
            style=discord.TextStyle.paragraph,
            default=current[:4000],
            max_length=4000,
            placeholder=ph
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        _smsg(interaction.guild.id, self.cfg["key"], self.field.value)
        await mlog(interaction.guild, "Nachrichten Config",
            str(interaction.user) + " hat **" + self.cfg["label"] + "** aktualisiert.")
        await _return_to_messages(interaction,
            " **" + self.cfg["label"] + "** wurde gespeichert.")


async def _return_to_messages(interaction: discord.Interaction, notice: str = ""):
    g = interaction.guild
    lines = []
    for m in _ALL_MESSAGES:
        current = _cmsg(g.id, m["key"])
        ch = _get_channel_for_msg(g, m)
        ch_str = ch.mention if ch else ""
        preview = (current[:55] + "…") if current and len(current) > 55 else (current or "*Standard*")
        lines.append("**" + m["label"] + "** → " + ch_str + "\n`" + preview + "`")

    desc_parts = []
    if notice:
        desc_parts.append(notice)
    desc_parts.append("Wähle eine Nachricht aus dem Dropdown um sie zu bearbeiten.\n")
    desc_parts.extend(lines)

    embed = discord.Embed(
        title=" Bot-Nachrichten",
        description="\n\n".join(desc_parts),
        color=0x2B2D31
    )
    embed.set_footer(
        text="Von " + str(interaction.user),
        icon_url=interaction.user.display_avatar.url
    )
    await interaction.response.edit_message(embed=embed, view=MessagesMainView(g.id))


@bot.tree.command(
    name="messages",
    description="Alle Bot-Nachrichten anzeigen und bearbeiten.",
    guild=discord.Object(id=ALLOWED_GUILD_ID)
)
async def messages_cmd(interaction: discord.Interaction):
    if interaction.user.id not in OWNERS:
        return await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
    g = interaction.guild
    lines = []
    for m in _ALL_MESSAGES:
        current = _cmsg(g.id, m["key"])
        ch = _get_channel_for_msg(g, m)
        ch_str = ch.mention if ch else ""
        preview = (current[:55] + "…") if current and len(current) > 55 else (current or "*Standard*")
        lines.append("**" + m["label"] + "** → " + ch_str + "\n`" + preview + "`")

    embed = discord.Embed(
        title=" Bot-Nachrichten",
        description="Wähle eine Nachricht aus dem Dropdown um sie zu bearbeiten.\n\n" + "\n\n".join(lines),
        color=0x2B2D31
    )
    embed.set_footer(
        text="Von " + str(interaction.user),
        icon_url=interaction.user.display_avatar.url
    )
    await interaction.response.send_message(embed=embed, view=MessagesMainView(g.id), ephemeral=True)

# ================================================================
#  ON READY
# ================================================================

@bot.event
async def on_ready():
    print(f"Online: {bot.user}")
    try:
        await bot.tree.sync(guild=discord.Object(id=ALLOWED_GUILD_ID))
    except: pass
    asyncio.create_task(voice_loop())
    asyncio.create_task(tracker_cleanup())
    asyncio.create_task(night_mode_loop())
    # Load night mode state
    for guild in bot.guilds:
        _night_mode_enabled[guild.id] = _night_mode_get_enabled(guild.id)
        # Restore saved perms from DB into memory
        night_role_ids = _night_roles_get(guild.id) or NIGHT_MODE_ROLES
        for rid in night_role_ids:
            saved = _night_perms_load_db(guild.id, rid)
            if saved:
                _night_saved_perms[rid] = saved
    for g in bot.guilds:
        c, lu = _cnt_load(g.id)
        counting_state["current"]   = c
        counting_state["last_user"] = lu or None
    for g in bot.guilds:
        try:
            invs = await g.invites()
            invite_cache[g.id] = {i.code: i.uses for i in invs}
        except: pass

# ================================================================
#  RUN
# ================================================================

bot.run(TOKEN)
