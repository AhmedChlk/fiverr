const { Telegraf } = require("telegraf");
const axios = require("axios");
const { exec } = require("child_process");

// ====== CONFIG DIRECTA (reemplaza con tus valores) ======
const TELEGRAM_BOT_TOKEN = "8066101181:AAHiIPPxVT5YXpJoTW7YJn14r_o3XtskfnY";
const GITHUB_TOKEN = "";
const TELEGRAM_CHAT_ID = ""; // solo como fallback
const GITHUB_REPO_OWNER = "AhmedChlk";
const GITHUB_REPO_NAME = "fiverr";
// ========================================================

const bot = new Telegraf(TELEGRAM_BOT_TOKEN);

// Helpers GitHub (por-usuario)
function filePathForChat(chatId) {
  return `playlists/${chatId}.txt`;
}

async function getPlaylistsFromGitHub(chatId) {
  const path = filePathForChat(chatId);
  try {
    const res = await axios.get(
      `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`,
      { headers: { Authorization: `token ${GITHUB_TOKEN}` } }
    );
    const content = Buffer.from(res.data.content, "base64").toString("utf-8");
    return { content, sha: res.data.sha, path };
  } catch (err) {
    if (err.response && err.response.status === 404) {
      // no existe aún → lista vacía
      return { content: "", sha: null, path };
    }
    throw err;
  }
}

async function updatePlaylistsInGitHub(chatId, newContent, sha) {
  const path = filePathForChat(chatId);
  const data = {
    message: `Update ${path} from Telegram bot`,
    content: Buffer.from(newContent).toString("base64"),
    sha: sha || undefined,
  };
  await axios.put(
    `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`,
    data,
    { headers: { Authorization: `token ${GITHUB_TOKEN}` } }
  );
}

// Ejecutar scraper local SOLO para el chat actual
async function runLocalScraping(chatId) {
  return new Promise((resolve, reject) => {
    const command = `node scrape.js`;
    const env = {
      ...process.env,
      TELEGRAM_BOT_TOKEN,
      TELEGRAM_CHAT_ID: String(chatId), // scraper usará este chat como destino Y como clave de lista
      CHAT_ID: String(chatId),
      GITHUB_TOKEN,
    };
    exec(command, { env }, (error, stdout, stderr) => {
      if (error) return reject(`Error scraping: ${error.message}`);
      if (stderr) console.error(`scraper STDERR: ${stderr}`);
      console.log(`scraper STDOUT: ${stdout}`);
      resolve("Scraping completado.");
    });
  });
}

// ----------------- Comandos -----------------
bot.command("add", async (ctx) => {
  const chatId = ctx.chat.id;
  const newUrl = ctx.message.text.split(" ")[1];
  if (!newUrl) return ctx.reply("Uso: /add [URL]");
  if (!newUrl.startsWith("https://app.artist.tools/playlist/")) {
    return ctx.reply("Debe ser una URL válida de app.artist.tools");
  }
  try {
    const { content, sha } = await getPlaylistsFromGitHub(chatId);
    const urls = content
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (urls.includes(newUrl)) return ctx.reply("Esa URL ya está en la lista.");
    urls.push(newUrl);
    await updatePlaylistsInGitHub(chatId, urls.join("\n"), sha);
    ctx.reply(`✅ URL añadida.\nTotal en tu lista: ${urls.length}`);
  } catch (e) {
    console.error("add error:", e.response?.data || e.message);
    ctx.reply("❌ Error al añadir la URL.");
  }
});

bot.command("remove", async (ctx) => {
  const chatId = ctx.chat.id;
  const urlToRemove = ctx.message.text.split(" ")[1];
  if (!urlToRemove) return ctx.reply("Uso: /remove [URL]");
  try {
    const { content, sha } = await getPlaylistsFromGitHub(chatId);
    const urls = content
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const newUrls = urls.filter((u) => u !== urlToRemove);
    if (newUrls.length === urls.length)
      return ctx.reply("Esa URL no está en tu lista.");
    await updatePlaylistsInGitHub(chatId, newUrls.join("\n"), sha);
    ctx.reply(`🗑️ URL eliminada.\nQuedan: ${newUrls.length}`);
  } catch (e) {
    console.error("remove error:", e.response?.data || e.message);
    ctx.reply("❌ Error al eliminar la URL.");
  }
});

bot.command("list", async (ctx) => {
  const chatId = ctx.chat.id;
  try {
    const { content } = await getPlaylistsFromGitHub(chatId);
    const clean = content.trim();
    if (!clean) return ctx.reply("📂 Tu lista está vacía.");
    ctx.reply(`📋 Tus playlists:\n\n${clean}`);
  } catch (e) {
    console.error("list error:", e.response?.data || e.message);
    ctx.reply("❌ Error al obtener tu lista.");
  }
});

bot.command("scrape_now", async (ctx) => {
  const chatId = ctx.chat.id;
  try {
    ctx.reply("⏳ Iniciando scraping de *tu* lista...");
    await runLocalScraping(chatId);
  } catch (e) {
    console.error("scrape_now error:", e);
    ctx.reply("❌ Error al iniciar scraping en la Mac Mini.");
  }
});

bot.start((ctx) => {
  ctx.reply(
    `¡Hola! Bot de scraping de artist.tools (listas por usuario).

Comandos:
/add [URL] - Añadir playlist
/remove [URL] - Eliminar playlist
/list - Ver todas
/scrape_now - Ejecutar scraping ahora`
  );
});

bot.launch();
console.log("Bot de Telegram iniciado (listas por usuario)...");
