import os
import json
import re
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands
from dotenv import load_dotenv
from discord.ext import tasks
from discord.ext import commands


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

raw_boss_ids = os.getenv("BOSS_ID", "")
BOSS_IDS = {
    s.strip().lstrip("{").rstrip("}")
    for s in raw_boss_ids.split(",")
    if s.strip()
}


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

LINKS_FILE = "links.json"

class ProfileNotFound(Exception):
    """AoE2Insights 帳號不存在時丟出這個錯誤"""
    pass

#查詢links.json裡面資料
def load_links():
    if not os.path.exists(LINKS_FILE):
        return {}
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def is_boss():
    async def predicate(ctx: commands.Context):
        return str(ctx.author.id) in BOSS_IDS
    return commands.check(predicate)

def save_links(data):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

with open("elo_roles.json", "r", encoding="utf-8") as f:
    ELO_RULES = json.load(f)


# AoE2 段位角色名稱清單（用來判斷哪些要移除）
AOE2_ROLE_NAMES = [v["role"] for v in ELO_RULES.values()]

def elo_to_role_data(elo: int) -> dict: # 回傳使用者分數 elo_to_role_data(1v1天梯分數)
    for limit_str, data in sorted(ELO_RULES.items(), key=lambda x: int(x[0])):
        if elo <= int(limit_str):
            return data
    return list(ELO_RULES.values())[-1]

def extract_profile_id(text: str) -> str | None:
    # 如果是網址，就用正則抓數字
    m = re.search(r"/user/(\d+)", text)
    if m:
        return m.group(1)

    # 如果就是一串數字，當成 ID
    if text.isdigit():
        return text

    return None

def fetch_1v1_rm_rating(profile_id: str) -> int:
    url = f"https://www.aoe2insights.com/user/{profile_id}/"
    resp = requests.get(url, timeout=10)

    # 1) HTTP 404：直接視為帳號不存在
    if resp.status_code == 404:
        raise ProfileNotFound(f"profile {profile_id} not found (HTTP 404)")

    # 其他不是 200 的狀況，也先當作錯誤丟出去
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 2) 頁面雖然不是 404，但內容是 "#not found"
    not_found_title = soup.find(string=re.compile(r"#\s*not\s*found", re.IGNORECASE))
    if not_found_title:
        raise ProfileNotFound(f"profile {profile_id} not found (page says #not found)")

    # === 以下才是正常抓分數的流程 ===
    mode_label = soup.find(string=re.compile(r"\b1v1 RM\b"))
    if not mode_label:
        # 沒找到 1v1 RM 這個卡片，可能這帳號沒打排位
        raise ValueError("找不到 1v1 RM 模式（可能沒有打 1v1 排位）")

    card = mode_label.find_parent("div", class_=re.compile(r"\bcard\b"))

    rating_small = card.find("small", string=re.compile(r"Rating"))
    m = re.search(r"(\d+)", rating_small.get_text())
    if not m:
        raise ValueError("Rating 裡面沒有數字")

    return int(m.group(1))

def verify_profile_exists(profile_id: str) -> bool:
    url = f"https://www.aoe2insights.com/user/{profile_id}/"
    resp = requests.get(url, timeout=10)

    # 1) 確認 HTTP 404
    if resp.status_code == 404:
        return False

    soup = BeautifulSoup(resp.text, "html.parser")

    # 2) HTML 內文出現 #not found
    not_found_title = soup.find(string=re.compile(r"#\s*not\s*found", re.IGNORECASE))
    if not_found_title:
        return False

    # 3) 其他狀況視為存在
    return True


@bot.event #bot上線提示
async def on_ready():
    print(f"✅ Bot 已上線：{bot.user} (ID: {bot.user.id})")

    # 只在「尚未啟動」時呼叫 start()
    if not auto_update_roles.is_running():
        auto_update_roles.start()
        print("⏳ 自動批次更新身分組任務啟動")
    else:
        print("⏳ 自動批次更新身分組任務已在執行中)")

@bot.event
async def on_command_error(ctx, error):
    # 少參數的情況（例如只打 !link）
    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name == "link":
            await ctx.send("❌ 你少打參數了喔！\n歐乃該請使用以下正確用法：`!link 你的AoE2Insights網址或ID`")
        elif ctx.command and ctx.command.name == "adminlink":
            await ctx.send("❌ 你少打參數了喔！\n歐乃該請使用以下正確用法：`!adminlink @某人 他的AoE2Insights網址或ID`")
        else:
            await ctx.send("❌ 這個指令少了必要參數。")
        return

    # 權限不夠（例如不是 BOSS 在用 adminlink）
    if isinstance(error, commands.CheckFailure):
        await ctx.send("這個指令只有某些人才可以用!")
        return

    # 指令不存在（打錯字的 !scroe 之類）
    if isinstance(error, commands.CommandNotFound):
        # 想安靜忽略就 pass，不想洗頻道
        return

    # 其他沒預期到的錯誤 → 先印出來方便 debug，再給一個通用訊息
    print("Command error:", repr(error))
    await ctx.send("⚠️ 指令執行時發生錯誤，請稍後再試或找 Tank20089 QQ")

@bot.command()
async def verify(ctx,profile:str): #!verify 網址
    profile_id = extract_profile_id(profile)
    exists = verify_profile_exists(profile_id)
    if not exists:
        await ctx.send("❌ 網址不存在, 請重新入一次!")
        return
    await ctx.send(f"✅ 網址存在!")

async def update_one_user(ctx: commands.Context, member: discord.Member):
    """抓該 member 的 AoE2Insights 分數，並更新段位。"""

    links = load_links()
    discord_id = str(member.id)

    if discord_id not in links:
        await ctx.send(f"{member.mention} 還沒有綁定 AoE2Insights 帳號。")
        return

    profile_id = links[discord_id]

    try:
        elo = fetch_1v1_rm_rating(profile_id)
    except ProfileNotFound:
        await ctx.send(f"❌ 查無此玩家（AoE2Insights 顯示不存在）ID = `{profile_id}`")
        return
    except ValueError:
        await ctx.send(f"⚠️ 找不到該玩家的 1v1 RM 排位（可能沒打排位）")
        return
    except Exception as e:
        await ctx.send(f"⚠️ 抓取資料時發生未知錯誤：{e}")
        return

    await update_score(member, elo)
    await ctx.send(
        f"🎯 **{member.display_name} 的 1v1 RM 分數是：`{elo}`**，"
        f"目前段位：{elo_to_role_data(elo)['role']} 獎牌: {elo_to_role_data(elo)['emoji']}"
    )

@bot.command()
async def ping(ctx):#輸入!ping 輸出pong！(from bot_Aoe2)
    await ctx.send("pong！(from bot_Aoe2)")
@bot.command()
async def myid(ctx):#輸入!myid 輸出您discord ID
    await ctx.send(f"你的 Discord ID 是 {ctx.author.id}")

@bot.command()
async def score(ctx, user: discord.Member | None = None):
    target = user or ctx.author
    await update_one_user(ctx, target)


@bot.command()
async def link(ctx, profile: str):#使用者綁定 AoE2 帳號 輸入!link "url(aoe2insights)"
    """
    用法：
    !link 589368
    !link https://www.aoe2insights.com/user/589368/
    """
    profile_id = extract_profile_id(profile)
    exists = verify_profile_exists(profile_id)
    if not exists:
        await ctx.send("❌ 網址不存在 或是 格式錯誤, 請重新入一次! \n 正確範例：`!link 589368` 或 `!link https://www.aoe2insights.com/user/589368/`")
        return
    if not profile_id:
        await ctx.send("❌ 網址格式錯誤  \n 正確範例：`!link 589368` 或 `!link https://www.aoe2insights.com/user/589368/`")
        return

    links = load_links()
    discord_id = str(ctx.author.id)

    links[discord_id] = profile_id
    save_links(links)
    await update_one_user(ctx, ctx.author)
    await ctx.send(f"已幫 <@{discord_id}> 綁定 AoE2Insights 帳號 ID！")
    
@bot.command()
#依照分數自動更新該使用者的段位身分組
async def update_score(member: discord.Member, elo: int):
    guild = member.guild

    role_data = elo_to_role_data(elo)
    role_name = role_data["role"]
    emoji = role_data["emoji"]

    # ✅ 用 role_name（字串）
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await guild.create_role(name=role_name)

    # 移除舊 AoE2 段位
    old_roles = [r for r in member.roles if r.name in AOE2_ROLE_NAMES]
    if old_roles:
        await member.remove_roles(*old_roles)

    # 加上新段位
    await member.add_roles(role)

    
@tasks.loop(minutes=60)
async def auto_update_roles():
    print("⏳ 自動批次更新身分組中...")
    links = load_links()

    for discord_id, profile_id in links.items():
        guild = bot.guilds[0]   # 只有一個伺服器就用 [0]

        member = guild.get_member(int(discord_id))
        if member is None:
            continue

        try:
            elo = fetch_1v1_rm_rating(profile_id)
            await update_score(member, elo)
            print(f"✔ 已更新 {member.name} → {elo}")
        except Exception as e:
            print(f"❌ 更新 {discord_id} 時發生錯誤: {e}")

@bot.command()
@is_boss()
#管理者幫忙登記某個人 AoE2 帳號 輸入 !link @某個人 "url(aoe2insights)"
async def adminlink(ctx, member: discord.Member, profile: str):
    """
    只有 .env 裡 BOSS_ID 清單的人可以用：
    !adminlink @某人 589368
    !adminlink @某人 https://www.aoe2insights.com/user/589368/
    """
    profile_id = extract_profile_id(profile)
    exists = verify_profile_exists(profile_id)
    if not exists:
        await ctx.send("❌ 網址不存在 或是 格式錯誤, 請重新入一次! \n 正確範例：`!adminlink @Ray.bb 3493625` 或 `!adminlink @Ray.bb https://www.aoe2insights.com/user/3493625/`")
        return
    if not profile_id:
        await ctx.send("❌ 網址格式錯誤  \n 正確範例：`!adminlink @Ray.bb 3493625` 或 `!adminlink @Ray.bb https://www.aoe2insights.com/user/3493625/`")
        return

    links = load_links()
    discord_id = str(member.id)

    links[discord_id] = profile_id
    save_links(links)
    await update_one_user(ctx,member)  
    await ctx.send(f"✅ 已幫 {member.mention} 綁定 AoE2Insights ID `{profile_id}`")

#管理者幫忙刪除某個人 AoE2 帳號 輸入 !link @某個人 "url(aoe2insights)"
@bot.command()
@is_boss()
async def admindel(ctx, member: discord.Member):
    discord_id = str(member.id)
    links = load_links()

    if discord_id in links:
        del links[discord_id]
        save_links(links)

    # 刪除段位角色

    remove_roles = [r for r in member.roles if r.name in AOE2_ROLE_NAMES]
    if remove_roles:
        await member.remove_roles(*remove_roles)

    await ctx.send(f"🗑️ 已刪除 {member.mention} 的綁定與段位身分組。")
    
bot.run(TOKEN)


