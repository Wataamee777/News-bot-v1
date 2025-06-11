import discord
from discord import app_commands
from discord.ext import tasks,commands
import feedparser
import json
import os
from aiohttp import web
import asyncio

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

bot = commands.Bot(command_prefix="/", intents=intents)

FEED_FILE = "feeds.json"
SENT_LINKS_FILE = "sent_links.json"

RSS_SOURCES = {
    "ダイヤモンド社": "https://diamond.jp/feed/",
    "日経BP社": "https://www.nikkeibp.co.jp/article/news/20081006/102677/",
    "日経TRENDY": "https://trendy.nikkeibp.co.jp/tools/rss/index.html",
    "朝日新聞社": "https://www.asahi.com/information/service/rss.html",
    "NHKニュース": "https://www3.nhk.or.jp/toppage/rss/index.html",
    "NHKオンライン": "https://www3.nhk.or.jp/toppage/rss/index2.html",
    "経済レポート": "https://www3.keizaireport.com/category.php/rss/",
    "中部経済新聞": "https://www.chukei-news.co.jp/index.xml",
    "日経ビジネス": "https://business.nikkeibp.co.jp/rss/all_nbo.rdf",
    "ECO JAPAN（nikkei）": "https://eco.nikkeibp.co.jp/rss/eco/eco.rdf",
    "ケンプラッツ": "https://kenplatz.nikkeibp.co.jp/article/knp/20071204/513934/",
    "読売新聞社": "https://www.yomiuri.co.jp/tools/rss/"
}

feeds: dict[str, list[str]] = {}  # {channel_id_str: [rss_url_str, ...], ...}
sent_links: dict[str, set[str]] = {}  # {channel_id_str: set(link_str, ...), ...}

def load_json(filename: str):
    if os.path.exists(filename):
        try:
            with open(filename, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"JSON読み込みエラー {filename}: {e}")
            return {}
    return {}

def save_json(filename: str, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"JSON保存エラー {filename}: {e}")

def load_data():
    global feeds, sent_links
    feeds = load_json(FEED_FILE)
    raw_sent = load_json(SENT_LINKS_FILE)
    sent_links.clear()
    for ch_id, links in raw_sent.items():
        if isinstance(links, list):
            sent_links[ch_id] = set(links)
        else:
            sent_links[ch_id] = set()

def save_data():
    save_json(FEED_FILE, feeds)
    sent_links_serializable = {k: list(v) for k, v in sent_links.items()}
    save_json(SENT_LINKS_FILE, sent_links_serializable)

@tasks.loop(minutes=60)
async def check_and_send_news():
    for channel_id_str, rss_urls in feeds.items():
        try:
            channel = client.get_channel(int(channel_id_str))
            # send()可能なチャンネルかチェック
            if channel is None or not isinstance(channel, discord.abc.Messageable):
                continue

            sent = sent_links.setdefault(channel_id_str, set())
            for rss_url in rss_urls:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries[:3]:
                    if entry.link not in sent:
                        msg = f"📰 **{entry.title}**\n{entry.link}"
                        try:
                            await channel.send(msg)
                        except Exception as e:
                            print(f"送信エラー {channel_id_str}: {e}")
                        else:
                            sent.add(entry.link)
            save_data()
        except Exception as e:
            print(f"[RSS送信ループエラー] チャンネル:{channel_id_str} - {e}")

RSS_CHOICES = [
    app_commands.Choice(name=name, value=url)
    for name, url in RSS_SOURCES.items()
]

@tree.command(name="add", description="このチャンネルに配信したいRSSを登録します")
@app_commands.describe(source="RSSを選んでください")
@app_commands.choices(source=RSS_CHOICES)
async def add(interaction: discord.Interaction, source: app_commands.Choice[str]):
    ch_id = str(interaction.channel_id)
    if ch_id not in feeds:
        feeds[ch_id] = []
    if source.value in feeds[ch_id]:
        await interaction.response.send_message("⚠️ すでに登録済みです。", ephemeral=True)
        return
    feeds[ch_id].append(source.value)
    save_data()
    await interaction.response.send_message(f"✅ {source.name} のRSSを登録しました。", ephemeral=True)

@tree.command(name="list", description="このチャンネルの登録RSS一覧を表示")
async def list_feeds(interaction: discord.Interaction):
    ch_id = str(interaction.channel_id)
    urls = feeds.get(ch_id, [])
    if not urls:
        await interaction.response.send_message("📭 登録RSSはありません。", ephemeral=True)
        return
    msg = "📋 登録済みRSS一覧:\n"
    for url in urls:
        name = next((k for k,v in RSS_SOURCES.items() if v == url), url)
        msg += f"・{name}\n"
    await interaction.response.send_message(msg, ephemeral=True)

@tree.command(name="remove", description="このチャンネルからRSS登録を削除します")
async def remove(interaction: discord.Interaction):
    ch_id = str(interaction.channel_id)
    if ch_id not in feeds or not feeds[ch_id]:
        await interaction.response.send_message("❌ 登録RSSがありません。", ephemeral=True)
        return

    options = [
        discord.SelectOption(label=next((k for k,v in RSS_SOURCES.items() if v == url), url), value=url)
        for url in feeds[ch_id]
    ]

    class RemoveView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.select = discord.ui.Select(
                placeholder="削除したいRSSを選んでください",
                min_values=1,
                max_values=len(options),
                options=options
            )
            self.select.callback = self.select_callback
            self.add_item(self.select)

        async def select_callback(self, interaction: discord.Interaction):
            for val in self.select.values:
                if val in feeds[ch_id]:
                    feeds[ch_id].remove(val)
            save_data()
            await interaction.response.edit_message(content="✅ 削除しました。", view=None)

    await interaction.response.send_message("🗑 削除するRSSを選択してください。", view=RemoveView(), ephemeral=True)
    
@bot.tree.command(name="help", description="コマンド一覧を表示します")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛠️ コマンド一覧",
        description=(
            "**/add [項目]**\n項目を一時リストに追加します。\n\n"
            "**/remove [項目]**\nリストから指定項目を削除します。\n\n"
            "**/list**\n現在追加されている項目を表示します。"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="※このメッセージはあなただけに表示されています。")
    await interaction.response.send_message(embed=embed, ephemeral=True)
    @tasks.loop(seconds=60)
    async def update_ping_status():
        latency = bot.latency * 1000  # 秒 → ミリ秒に変換
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=f"ニュースを視聴中 || ping値: {latency:.2f}ms"
        )
        await bot.change_presence(status=discord.Status.online, activity=activity)

@client.event
async def on_ready():
    print(f"✅ Botが起動しました: {client.user}")
    await tree.sync()
    if not check_and_send_news.is_running():
        check_and_send_news.start()

# aiohttp Webサーバー部分
async def handle_index(request):
    return web.Response(text="RSS配信Bot稼働中です。", content_type='text/plain')

async def handle_status(request):
    data = {ch: len(rss_list) for ch, rss_list in feeds.items()}
    return web.json_response(data)

async def init_webserver():
    app = web.Application()
    app.add_routes([
        web.get('/', handle_index),
        web.get('/status', handle_status)
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    try:
        await site.start()
        print("🌐 Webサーバー起動： http://0.0.0.0:8080")
    except Exception as e:
        print(f"Webサーバー起動失敗: {e}")

async def main():
    load_data()
    token = os.getenv("TOKEN")
    if not token:
        print("ERROR: 環境変数TOKENが設定されていません。")
        return
    await init_webserver()
    await client.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot停止しました。")
