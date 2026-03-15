import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import aiohttp
import re
import base64
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

async def ask_gemini(prompt: str, image_bytes: bytes = None, mime_type: str = None) -> str:
    """Send request to Gemini and return raw text response."""
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

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload
        ) as resp:
            data = await resp.json()

    # Log full response for debugging
    print(f"Gemini response: {data}")

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        # Check for errors
        if "error" in data:
            return f"API_ERROR: {data['error'].get('message', 'Unknown error')}"
        return "FAILED"

async def translate_text(arabic_text: str) -> dict:
    """Translate Arabic text to Urdu and English."""
    prompt = f"""Translate this Arabic text to Urdu and English.

Arabic: {arabic_text}

Reply in this exact format only:
URDU: [urdu translation]
ENGLISH: [english translation]"""

    response = await ask_gemini(prompt)
    print(f"Translation response: {response}")
    return parse_response(response)

async def translate_image(image_bytes: bytes, mime_type: str) -> dict:
    """Extract Arabic from image and translate."""
    prompt = """Look at this image. Extract any Arabic text you see, then translate it.

Reply in this exact format only:
ARABIC: [extracted arabic text, or NONE if no arabic found]
URDU: [urdu translation, or NONE]
ENGLISH: [english translation, or NONE]"""

    response = await ask_gemini(prompt, image_bytes, mime_type)
    print(f"Image response: {response}")
    return parse_response(response, include_arabic=True)

def parse_response(text: str, include_arabic: bool = False) -> dict:
    """Parse Gemini response."""
    result = {"urdu": "", "english": ""}
    if include_arabic:
        result["arabic"] = ""

    if text.startswith("API_ERROR:") or text == "FAILED":
        result["urdu"] = text
        result["english"] = text
        return result

    for line in text.strip().split('\n'):
        line = line.strip()
        if line.upper().startswith("ARABIC:"):
            result["arabic"] = line[7:].strip()
        elif line.upper().startswith("URDU:"):
            result["urdu"] = line[5:].strip()
        elif line.upper().startswith("ENGLISH:"):
            result["english"] = line[8:].strip()

    # If parsing failed, return full response as english
    if not result["english"] and not result["urdu"]:
        result["english"] = text[:500]
        result["urdu"] = "Could not parse response"

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
    print(f"Gemini API Key loaded: {'✅' if GEMINI_API_KEY else '❌ MISSING'}")

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

                if translations.get("arabic") == "NONE":
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
