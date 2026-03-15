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

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))

async def ask_gemini_text(arabic_text: str) -> dict:
    """Send Arabic text to Gemini and get Urdu + English translation."""
    prompt = f"""You are a translation assistant. The following text is in Arabic.
Please provide:
1. Urdu translation
2. English translation

Arabic text: {arabic_text}

Respond in exactly this format:
URDU: <urdu translation here>
ENGLISH: <english translation here>"""

    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload
        ) as resp:
            data = await resp.json()

    try:
        response_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return parse_translation(response_text)
    except (KeyError, IndexError):
        return {"urdu": "Translation failed", "english": "Translation failed"}

async def ask_gemini_image(image_bytes: bytes, mime_type: str) -> dict:
    """Send image to Gemini to extract Arabic text and translate it."""
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    prompt = """This image contains Arabic text. Please:
1. Extract all the Arabic text from the image
2. Translate it to Urdu
3. Translate it to English

Respond in exactly this format:
ARABIC: <extracted arabic text here>
URDU: <urdu translation here>
ENGLISH: <english translation here>

If no Arabic text is found, respond with:
ARABIC: NONE
URDU: NONE
ENGLISH: NONE"""

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_b64
                        }
                    },
                    {"text": prompt}
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload
        ) as resp:
            data = await resp.json()

    try:
        response_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return parse_translation(response_text, include_arabic=True)
    except (KeyError, IndexError) as e:
        print(f"Gemini error: {e} | Response: {data}")
        return {"arabic": "Failed", "urdu": "Translation failed", "english": "Translation failed"}

def parse_translation(text: str, include_arabic: bool = False) -> dict:
    """Parse Gemini response into structured dict."""
    result = {"urdu": "", "english": ""}
    if include_arabic:
        result["arabic"] = ""

    for line in text.strip().split('\n'):
        if line.startswith("ARABIC:"):
            result["arabic"] = line.replace("ARABIC:", "").strip()
        elif line.startswith("URDU:"):
            result["urdu"] = line.replace("URDU:", "").strip()
        elif line.startswith("ENGLISH:"):
            result["english"] = line.replace("ENGLISH:", "").strip()

    return result

def build_embed_text(original: str, translations: dict) -> discord.Embed:
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    embed.add_field(name="📝 Original Arabic", value=original[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text="Powered by Google Gemini 2.0 Flash ✨")
    return embed

def build_embed_image(translations: dict) -> discord.Embed:
    embed = discord.Embed(title="🌐 Image Translation", color=0x00f3ff)
    embed.add_field(name="📝 Extracted Arabic", value=translations.get("arabic", "—")[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text="Powered by Google Gemini 2.0 Flash ✨")
    return embed

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready! Using Gemini 2.0 Flash 🚀")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── 1. Arabic text detection ──────────────────────────────────────────────
    if message.content and contains_arabic(message.content):
        try:
            async with message.channel.typing():
                translations = await ask_gemini_text(message.content)
            embed = build_embed_text(message.content, translations)
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Translation error: {e}")

    # ── 2. Image processing ───────────────────────────────────────────────────
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                await message.add_reaction("⏳")

                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        image_bytes = await resp.read()
                        mime_type = attachment.content_type

                translations = await ask_gemini_image(image_bytes, mime_type)
                await message.remove_reaction("⏳", bot.user)

                if translations.get("arabic") == "NONE":
                    await message.reply("🖼️ No Arabic text found in this image.")
                    continue

                embed = build_embed_image(translations)
                await message.reply(embed=embed)

            except Exception as e:
                await message.remove_reaction("⏳", bot.user)
                await message.reply(f"⚠️ Image processing error: {e}")

    await bot.process_commands(message)

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="translate")
async def translate_command(ctx, *, text: str):
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text to translate.")
        return
    try:
        async with ctx.typing():
            translations = await ask_gemini_text(text)
        embed = build_embed_text(text, translations, )
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"⚠️ Translation error: {e}")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    bot.run(TOKEN)
