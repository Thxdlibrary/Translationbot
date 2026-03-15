import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import aiohttp
import re
import base64
import asyncio
from flask import Flask
from threading import Thread

# ── Keep-alive server ────────────────────────────────────────────────────────
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive! 🤖"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run_flask, daemon=True).start()

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))

async def ask_gemini(prompt: str, image_bytes: bytes = None, mime_type: str = None, retries: int = 3) -> str:
    """Send ONE request to Gemini with automatic retry on rate limit."""
    parts = []

    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": base64.b64encode(image_bytes).decode('utf-8')
            }
        })

    parts.append({"text": prompt})
    payload = {"contents": [{"parts": parts}]}

    for attempt in range(retries):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload
            ) as resp:
                data = await resp.json()

        # Check for rate limit error
        if "error" in data:
            error_msg = data["error"].get("message", "")
            print(f"Gemini error (attempt {attempt+1}): {error_msg}")
            if "quota" in error_msg.lower() or "rate" in error_msg.lower():
                wait_time = 15 * (attempt + 1)  # wait 15s, 30s, 45s
                print(f"Rate limited. Waiting {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            return f"ERROR: {error_msg}"

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return "FAILED"

    return "RATE_LIMIT"

async def translate_text(arabic_text: str) -> dict:
    """Translate Arabic text using ONE Gemini call."""
    prompt = f"""Translate this Arabic text to both Urdu and English.

Arabic text: {arabic_text}

Respond in EXACTLY this format:
URDU: [urdu translation here]
ENGLISH: [english translation here]"""

    response = await ask_gemini(prompt)
    return parse_response(response)

async def translate_image(image_bytes: bytes, mime_type: str) -> dict:
    """Extract Arabic from image and translate using ONE Gemini call."""
    prompt = """Extract any Arabic text from this image and translate it to Urdu and English.

Respond in EXACTLY this format:
ARABIC: [extracted arabic text, or NONE if no arabic found]
URDU: [urdu translation, or NONE]
ENGLISH: [english translation, or NONE]"""

    response = await ask_gemini(prompt, image_bytes, mime_type)
    return parse_response(response, include_arabic=True)

def parse_response(text: str, include_arabic: bool = False) -> dict:
    """Parse Gemini response into structured dict."""
    result = {"urdu": "", "english": ""}
    if include_arabic:
        result["arabic"] = ""

    if text in ["FAILED", "RATE_LIMIT"] or text.startswith("ERROR:"):
        msg = "⏳ Rate limit reached, please try again in 30 seconds." if text == "RATE_LIMIT" else text
        result["urdu"] = msg
        result["english"] = msg
        return result

    for line in text.strip().split('\n'):
        line = line.strip()
        if line.upper().startswith("ARABIC:"):
            result["arabic"] = line[7:].strip()
        elif line.upper().startswith("URDU:"):
            result["urdu"] = line[5:].strip()
        elif line.upper().startswith("ENGLISH:"):
            result["english"] = line[8:].strip()

    # Fallback if parsing fails
    if not result["urdu"] and not result["english"]:
        result["english"] = text[:500]
        result["urdu"] = "Could not parse"

    return result

def build_embed(original: str, translations: dict, is_image: bool = False) -> discord.Embed:
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    if is_image and translations.get("arabic"):
        embed.add_field(name="📝 Extracted Arabic", value=translations["arabic"][:1024] or "—", inline=False)
    else:
        embed.add_field(name="📝 Original Arabic", value=original[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text="Powered by Google Gemini 2.0 Flash ✨")
    return embed

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Gemini API Key: {'✅ Loaded' if GEMINI_API_KEY else '❌ MISSING'}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.content and contains_arabic(message.content):
        try:
            async with message.channel.typing():
                translations = await translate_text(message.content)
            embed = build_embed(message.content, translations)
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Error: {e}")

    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                await message.add_reaction("⏳")
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        image_bytes = await resp.read()

                translations = await translate_image(image_bytes, attachment.content_type)
                await message.remove_reaction("⏳", bot.user)

                if translations.get("arabic", "").upper() == "NONE":
                    await message.reply("🖼️ No Arabic text found in this image.")
                    continue

                embed = build_embed("", translations, is_image=True)
                await message.reply(embed=embed)
            except Exception as e:
                await message.reply(f"⚠️ Image error: {e}")

    await bot.process_commands(message)

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="translate")
async def translate_command(ctx, *, text: str):
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text.")
        return
    try:
        async with ctx.typing():
            translations = await translate_text(text)
        embed = build_embed(text, translations)
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"⚠️ Error: {e}")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    bot.run(TOKEN)
