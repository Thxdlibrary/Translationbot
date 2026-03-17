# v3.0 - Full OpenRouter (OCR + Translation)
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import aiohttp
import re
import base64
import asyncio
import time
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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PROXY_URL = os.getenv("PROXY_URL")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Vision models to try in order (best to fallback) - Updated March 2026
VISION_MODELS = [
    "qwen/qwen2.5-vl-72b-instruct:free",
    "qwen/qwen2.5-vl-32b-instruct:free",
    "moonshotai/kimi-vl-a3b-thinking:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "qwen/qwen2-vl-7b-instruct:free",
]

# Translation model
TRANSLATION_MODEL = "openrouter/auto"

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    proxy=PROXY_URL
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text):
    return bool(re.search(r'[\u0600-\u06FF]', text))

async def call_openrouter(messages, model, retries=3):
    """Generic OpenRouter API call with retry."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://discord-translation-bot.com",
        "X-Title": "Arabic Translation Bot"
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4000
    }

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json=payload
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                print(f"OpenRouter error [{model}] (attempt {attempt+1}): {error_msg}")
                if "rate" in error_msg.lower():
                    await asyncio.sleep(10)
                    continue
                return None

            content = data["choices"][0]["message"]["content"]
            print(f"✅ Model {model} responded successfully")
            return content

        except Exception as e:
            print(f"Exception [{model}] (attempt {attempt+1}): {e}")
            await asyncio.sleep(5)

    return None

async def extract_arabic_from_image(image_bytes, mime_type):
    """Try multiple vision models until one works."""
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_b64}"
                    }
                },
                {
                    "type": "text",
                    "text": "This image contains Arabic text from a scanned book or document. Please carefully extract ALL the Arabic text you can see, including any highlighted or bold text. Return ONLY the extracted Arabic text exactly as written, preserving the original text. If absolutely no Arabic text is found, return: NONE"
                }
            ]
        }
    ]

    # Try each vision model until one succeeds
    for model in VISION_MODELS:
        print(f"Trying vision model: {model}")
        result = await call_openrouter(messages, model)
        print(f"Raw result from {model}: '{str(result)[:200]}'")
        if result and result.strip().upper() != "NONE" and result.strip():
            print(f"✅ OCR succeeded with: {model}")
            return result.strip()
        else:
            print(f"❌ Model {model} returned empty or NONE")

    print("❌ All vision models failed")
    return ""

async def translate_text(arabic_text, language="both"):
    """Translate Arabic text using OpenRouter auto model."""
    if language == "urdu":
        prompt = f"""Translate the COMPLETE Arabic text below to Urdu.
Do not skip or truncate any part. Return ONLY the Urdu translation.

Arabic text:
{arabic_text}"""
    elif language == "english":
        prompt = f"""Translate the COMPLETE Arabic text below to English.
Do not skip or truncate any part. Return ONLY the English translation.

Arabic text:
{arabic_text}"""
    else:
        prompt = f"""Translate the COMPLETE Arabic text below to both Urdu and English.
Do not skip or truncate any part.

Arabic text:
{arabic_text}

Respond in EXACTLY this format:
URDU: [complete urdu translation]
ENGLISH: [complete english translation]"""

    messages = [
        {
            "role": "system",
            "content": "You are a professional Arabic translator. Translate accurately and completely without skipping any text."
        },
        {
            "role": "user",
            "content": prompt
        }
    ]

    response = await call_openrouter(messages, TRANSLATION_MODEL)

    if not response:
        return {"urdu": "Translation failed", "english": "Translation failed"}

    if language == "urdu":
        return {"urdu": response.strip(), "english": ""}
    elif language == "english":
        return {"urdu": "", "english": response.strip()}
    else:
        return parse_both(response)

def parse_both(text):
    result = {"urdu": "", "english": ""}
    for line in text.strip().split('\n'):
        line = line.strip()
        if line.upper().startswith("URDU:"):
            result["urdu"] = line[5:].strip()
        elif line.upper().startswith("ENGLISH:"):
            result["english"] = line[8:].strip()
    if not result["urdu"] and not result["english"]:
        result["english"] = text[:500]
        result["urdu"] = "Could not parse"
    return result

def add_long_field(embed, name, value):
    if not value or not value.strip():
        return
    chunks = [value[i:i+1024] for i in range(0, len(value), 1024)]
    for i, chunk in enumerate(chunks):
        embed.add_field(
            name=name if i == 0 else f"{name} (cont.)",
            value=chunk,
            inline=False
        )

async def get_image_arabic(ctx):
    """Download image and extract Arabic text."""
    attachment = ctx.message.attachments[0]
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        await ctx.reply("⚠️ Please attach a valid image.")
        return None


    async with aiohttp.ClientSession() as session:
        async with session.get(attachment.url) as resp:
            image_bytes = await resp.read()

    extracted = await extract_arabic_from_image(image_bytes, attachment.content_type)

    if not extracted or extracted.strip().upper() == "NONE":
        await ctx.reply("🖼️ No Arabic text found in this image.")
        return None

    return extracted

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌ MISSING'}")
    print(f"Proxy: {'✅ Enabled' if PROXY_URL else '❌ Not set'}")
    print(f"Vision models: {VISION_MODELS}")

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="urdu", aliases=["u", "ur"])
async def translate_urdu(ctx, *, text=None):
    """Translate Arabic to Urdu only"""
    if ctx.message.attachments:
        arabic = await get_image_arabic(ctx)
        if not arabic:
            return
        async with ctx.typing():
            translations = await translate_text(arabic, "urdu")
        embed = discord.Embed(title="🇵🇰 Urdu Translation", color=0x00f3ff)
        add_long_field(embed, "📝 Original Arabic", arabic)
        add_long_field(embed, "🇵🇰 Urdu", translations["urdu"])
        embed.set_footer(text="Powered by OpenRouter 🦙")
        await ctx.reply(embed=embed)
        return

    if not text:
        await ctx.reply("**Usage:** `!urdu <arabic text>` or attach image + `!urdu`")
        return
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text.")
        return
    async with ctx.typing():
        translations = await translate_text(text, "urdu")
    embed = discord.Embed(title="🇵🇰 Urdu Translation", color=0x00f3ff)
    add_long_field(embed, "📝 Original Arabic", text)
    add_long_field(embed, "🇵🇰 Urdu", translations["urdu"])
    embed.set_footer(text="Powered by OpenRouter 🦙")
    await ctx.reply(embed=embed)

@bot.command(name="english", aliases=["e", "en"])
async def translate_english(ctx, *, text=None):
    """Translate Arabic to English only"""
    if ctx.message.attachments:
        arabic = await get_image_arabic(ctx)
        if not arabic:
            return
        async with ctx.typing():
            translations = await translate_text(arabic, "english")
        embed = discord.Embed(title="🇬🇧 English Translation", color=0x00f3ff)
        add_long_field(embed, "📝 Original Arabic", arabic)
        add_long_field(embed, "🇬🇧 English", translations["english"])
        embed.set_footer(text="Powered by OpenRouter 🦙")
        await ctx.reply(embed=embed)
        return

    if not text:
        await ctx.reply("**Usage:** `!english <arabic text>` or attach image + `!english`")
        return
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text.")
        return
    async with ctx.typing():
        translations = await translate_text(text, "english")
    embed = discord.Embed(title="🇬🇧 English Translation", color=0x00f3ff)
    add_long_field(embed, "📝 Original Arabic", text)
    add_long_field(embed, "🇬🇧 English", translations["english"])
    embed.set_footer(text="Powered by OpenRouter 🦙")
    await ctx.reply(embed=embed)

@bot.command(name="translate", aliases=["t", "tr", "both", "b"])
async def translate_both_command(ctx, *, text=None):
    """Translate Arabic to both Urdu and English"""
    if ctx.message.attachments:
        arabic = await get_image_arabic(ctx)
        if not arabic:
            return
        async with ctx.typing():
            translations = await translate_text(arabic, "both")
        embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
        add_long_field(embed, "📝 Original Arabic", arabic)
        add_long_field(embed, "🇵🇰 Urdu", translations["urdu"])
        add_long_field(embed, "🇬🇧 English", translations["english"])
        embed.set_footer(text="Powered by OpenRouter 🦙")
        await ctx.reply(embed=embed)
        return

    if not text:
        await ctx.reply(
            "**📖 Quick Guide:**\n\n"
            "`!urdu <arabic>` — Urdu only\n"
            "`!english <arabic>` — English only\n"
            "`!translate <arabic>` — Both\n"
            "Attach image + any command for image translation\n\n"
            "Type `!guide` for full guide."
        )
        return

    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text.")
        return
    async with ctx.typing():
        translations = await translate_text(text, "both")
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    add_long_field(embed, "📝 Original Arabic", text)
    add_long_field(embed, "🇵🇰 Urdu", translations["urdu"])
    add_long_field(embed, "🇬🇧 English", translations["english"])
    embed.set_footer(text="Powered by OpenRouter 🦙")
    await ctx.reply(embed=embed)

@bot.command(name="guide", aliases=["h", "commands"])
async def guide(ctx):
    """Show full guide"""
    embed = discord.Embed(
        title="📖 Translation Bot — Full Guide",
        description="Translates Arabic text and images to Urdu and/or English.",
        color=0x00f3ff
    )
    embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="**📝 TEXT TRANSLATION**", inline=False)
    embed.add_field(name="🇵🇰 Urdu only", value="`!urdu <arabic text>`\n`!u <arabic text>`\nExample: `!urdu مرحبا`", inline=False)
    embed.add_field(name="🇬🇧 English only", value="`!english <arabic text>`\n`!e <arabic text>`\nExample: `!english مرحبا`", inline=False)
    embed.add_field(name="🌐 Both Urdu + English", value="`!translate <arabic text>`\n`!t <arabic text>`\nExample: `!translate مرحبا`", inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="**🖼️ IMAGE TRANSLATION**", inline=False)
    embed.add_field(name="🇵🇰 Image → Urdu only", value="Attach image + type `!urdu` or `!u`", inline=False)
    embed.add_field(name="🇬🇧 Image → English only", value="Attach image + type `!english` or `!e`", inline=False)
    embed.add_field(name="🌐 Image → Both", value="Attach image + type `!translate` or `!t`", inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="**⚙️ OTHER COMMANDS**", inline=False)
    embed.add_field(name="🏓 Check Status", value="`!ping`", inline=False)
    embed.add_field(name="📖 Show Guide", value="`!guide` or `!h`", inline=False)
    embed.set_footer(text="Powered by OpenRouter 🦙")
    await ctx.reply(embed=embed)

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    print("⏳ Waiting 5 seconds before connecting...")
    time.sleep(5)
    bot.run(
        TOKEN,
        reconnect=True,        # auto reconnect on disconnect
        log_handler=None       # reduce log noise
    )
