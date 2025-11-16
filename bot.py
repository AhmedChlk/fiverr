const { Telegraf } = require("telegraf");
const axios = require("axios");
const { exec } = require("child_process");
const cron = require("node-cron");
const fs = require("fs").promises;
const fsSync = require("fs"); // pour existsSync dans certains fallback
const path = require("path");

// ====== CONFIGURATION ======
const TELEGRAM_BOT_TOKEN = "8474909082:";
const GITHUB_TOKEN = "ghp_";
const GITHUB_REPO_OWNER = "565";
const GITHUB_REPO_NAME = "artist";

const bot = new Telegraf(TELEGRAM_BOT_TOKEN);

// ---- FILE PATHS ----
function filePathForChat(chatId) {
  return `playlist-data/${chatId}/urls.txt`;
}

// ---- LOCAL HELPERS ----
async function ensureDir(dirPath) {
  try {
    await fs.mkdir(dirPath, { recursive: true });
  } catch (err) {
    console.warn("⚠️ ensureDir error:", err.message);
  }
}

// ---- GITHUB / SCHEDULE HELPERS ----
async function saveScheduleToGitHub(chatId, payloadObj) {
  const pathRepo = `playlist-data/${chatId}/scraper-schedule.json`;
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${pathRepo}`;
  const contentStr = JSON.stringify(payloadObj, null, 2);
  const payload = {
    message: `Update scraper schedule for chat ${chatId}`,
    content: Buffer.from(contentStr).toString("base64"),
  };

  try {
    const getRes = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 8000,
    }).catch((e) => null);

    if (getRes && getRes.data && getRes.data.sha) {
      payload.sha = getRes.data.sha;
    }

    const res = await axios.put(url, payload, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    return { ok: true, sha: res.data.content?.sha || null };
  } catch (err) {
    console.error("❌ Failed to save schedule to GitHub:", err.message);
    throw err;
  }
}

// ---- GITHUB FUNCTIONS ----
async function getPlaylistsFromGitHub(chatId) {
  const pathRepo = filePathForChat(chatId);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${pathRepo}`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });

    const content = Buffer.from(res.data.content, "base64").toString("utf-8");

    //1 split + trim + enlever commentaires/vides
    const lines = content
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"));

    // 2) normaliser artist.tools -> app.artist.tools (comme avant)
    const mapped = lines.map((line) =>
      line.replace("https://artist.tools", "https://app.artist.tools")
    );

    // 3) garder seulement les URLs considérées valides par isValidPlaylistUrl
    const urls = mapped.filter((line) => isValidPlaylistUrl(line));

    // 4) extraire les hostnames/domains uniques des URLs valides
    const domains = Array.from(
      new Set(
        urls
          .map((u) => {
            try {
              return new URL(u).hostname;
            } catch (e) {
              return null;
            }
          })
          .filter(Boolean)
      )
    );

    return { urls, content, sha: res.data.sha, path: pathRepo, domains };
  } catch (err) {
    // Gestion propre du 404 : fichier absent => on retourne une liste vide au lieu de throw
    if (err.response && err.response.status === 404) {
      console.warn(`⚠️ getPlaylistsFromGitHub: ${pathRepo} not found (404). Returning empty list.`);
      return { urls: [], content: "", sha: null, path: pathRepo, domains: [] };
    }
    console.error("❌ GitHub playlist fetch error:", err.message);
    if (err.response) {
      console.error("Response data:", err.response.data);
      console.error("Response status:", err.response.status);
      console.error("Response headers:", err.response.headers);
    }
    throw err;
  }
}




async function updatePlaylistsInGitHub(chatId, urls, sha) {
  const pathRepo = filePathForChat(chatId);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${pathRepo}`;
  const header = `# Playlist URLs for Chat ID: ${chatId}\n# Add one URL per line\n# Format: https://app.artist.tools/playlist/ID\n\n`;
  const payload = {
    message: `Update playlist file for chat ${chatId}`,
    content: Buffer.from(header + urls.join("\n")).toString("base64"),
  };
  if (sha) payload.sha = sha;

  try {
    const res = await axios.put(url, payload, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    return { ok: true, sha: res.data.content?.sha || null };
  } catch (err) {
    console.error("❌ GitHub playlist update error:", err.message);
    throw err;
  }
}

// ---- URL VALIDATION ----
function isValidPlaylistUrl(url) {
  return (
    url &&
    (url.startsWith("https://app.artist.tools/playlist/") ||
      url.startsWith("https://artist.tools/playlist/") ||
      url.startsWith("https://open.spotify.com/playlist/"))
  );
}

function normalizeUrl(url) {
  if (url.startsWith("https://artist.tools/playlist/")) {
    return url.replace("https://artist.tools", "https://app.artist.tools");
  }
  const spotifyMatch = url.match(
    /open\.spotify\.com\/playlist\/([a-zA-Z0-9]+)(?:\?|$)/
  );
  if (spotifyMatch) return `https://app.artist.tools/playlist/${spotifyMatch[1]}`;
  return url;
}


async function runLocalScraping(chatId) {
  const localDir = path.join("data", String(chatId));
  //await ensureDir(localDir);
  
  return new Promise((resolve, reject) => {
    const command = `node scrape.js`;
    const env = {
      ...process.env,
      TELEGRAM_BOT_TOKEN,
      TELEGRAM_CHAT_ID: String(chatId),
      CHAT_ID: String(chatId),
      GITHUB_TOKEN,
    };
    
   
    
    const child = exec(command, {
      env,
      timeout: 15 * 60 * 1000  
    }, (error, stdout, stderr) => {
      if (error) {
        console.error(`❌ Scrape error: ${error.message}`);
        return reject(new Error(`Scraping failed: ${error.message}`));
      }
      
      if (stderr) console.warn(`⚠️ Scrape stderr: ${stderr}`);
      
      resolve("Scraping completed");
    });
    
    child.on("error", (err) => {
      console.error(`❌ Child process error: ${err.message}`);
      reject(new Error(`Child process error: ${err.message}`));
    });
    
    child.on("timeout", () => {
      console.error(`❌ Scrape timeout after 15 minutes`);
      child.kill('SIGKILL');
      reject(new Error(`Scraping timeout after 15 minutes`));
    });
    
    child.stdout.on('data', (data) => {
      console.log(`🚀 Scrape stdout: ${data}`);
    });
    
    child.stderr.on('data', (data) => {
      console.warn(`⚠️ Scrape stderr: ${data}`);
    });
    
    child.on('close', (code, signal) => {
      console.log(`📄 Child process closed with code ${code} and signal ${signal}`);
     
    });
    
    child.on('exit', (code, signal) => {
      console.log(`🚪 Child process exited with code ${code} and signal ${signal}`);
     
    });
  });
}



// ---- SCHEDULE HANDLING ----
const SCHEDULE_PATH = "scraper-schedule.json";

// Sistema de schedules por usuario (nueva funcionalidad)
const userSchedules = new Map(); // chatId -> { task, schedule, sha }

// Función nueva: obtener schedule por usuario
async function getScheduleFromRepoForUser(chatId) {
  const userSchedulePath = `playlist-data/${chatId}/schedule.json`;
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${userSchedulePath}`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    const content = Buffer.from(res.data.content, "base64").toString("utf-8");
    const parsed = JSON.parse(content);
    return {
      ...{ time: "09:00", enabled: true },
      ...parsed,
      sha: res.data.sha,
    };
  } catch (err) {
    if (err.response?.status === 404) {
      return { time: "09:00", enabled: true, sha: null };
    }
    console.error(`❌ User schedule fetch error for ${chatId}:`, err.message);
    throw err;
  }
}

// Función original mantenida para compatibilidad
async function getScheduleFromRepo() {
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${SCHEDULE_PATH}`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    const content = Buffer.from(res.data.content, "base64").toString("utf-8");
    const parsed = JSON.parse(content);
    return {
      ...{ time: "09:00", enabled: true },
      ...parsed,
      sha: res.data.sha,
    };
  } catch (err) {
    if (err.response?.status === 404) {
      return { time: "09:00", enabled: true, sha: null };
    }
    console.error("❌ Schedule fetch error:", err.message);
    throw err;
  }
}

// Función nueva: actualizar schedule por usuario
async function updateScheduleInRepoForUser(chatId, time, enabled, sha) {
  const userSchedulePath = `playlist-data/${chatId}/schedule.json`;
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${userSchedulePath}`;
  const payload = {
    message: `Update user schedule for ${chatId} to ${time} (enabled: ${enabled})`,
    content: Buffer.from(JSON.stringify({ time, enabled }, null, 2)).toString("base64"),
  };
  if (sha) payload.sha = sha;
  try {
    await axios.put(url, payload, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    await saveScheduleToGitHub(chatId, { time, enabled }); 
    console.log(`✅ Updated schedule for user ${chatId}`);
  } catch (err) {
    console.error(`❌ User schedule update error for ${chatId}:`, err.message);
    await saveScheduleToGitHub(chatId, { time, enabled });
    throw err;
  }
}

// Función original mantenida para compatibilidad
async function updateScheduleInRepo(time, enabled, sha) {
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${SCHEDULE_PATH}`;
  const payload = {
    message: `Update scraper schedule to ${time} (enabled: ${enabled})`,
    content: Buffer.from(JSON.stringify({ time, enabled }, null, 2)).toString("base64"),
  };
  if (sha) payload.sha = sha;
  try {
    await axios.put(url, payload, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    await saveScheduleToGitHub("system", { time, enabled }); 
    console.log("✅ Updated schedule");
  } catch (err) {
    console.error("❌ Schedule update error:", err.message);
    await saveScheduleToGitHub("system", { time, enabled });
    throw err;
  }
}

async function getAllChatIdsFromRepo() {
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/playlist-data`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    return res.data.filter((entry) => entry.type === "dir").map((d) => d.name);
  } catch (err) {
    if (err.response?.status === 404) {
      const base = "playlist-data";
      if (!fsSync.existsSync(base)) return [];
      const entries = await fs.readdir(base, { withFileTypes: true });
      return entries.filter((e) => e.isDirectory()).map((d) => d.name);
    }
    console.error("❌ Chat IDs fetch error:", err.message);
    throw err;
  }
}

async function triggerScrapeNowForChat(chatId) {
  try {
    const fakeCtx = {
      chat: { id: chatId },
      reply: async (text, options) => {
        await axios.post(
          `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
          {
            chat_id: chatId,
            text: text.slice(0, 4096),
            parse_mode: options?.parse_mode || "HTML",
          },
          { timeout: 10000 }
        );
        console.log(`📤 Sent message to chat ${chatId}`);
      },
    };
    await scrapeNowHandler(fakeCtx);
  } catch (err) {
    console.error(`❌ Scrape trigger error for ${chatId}:`, err.message);
    await axios
      .post(
        `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
        {
          chat_id: chatId,
          text: `❌ <b>Auto-scrape error</b>\n🛠️ Details: ${err.message}\n🔄 Retry with /check`,
          parse_mode: "HTML",
        },
        { timeout: 10000 }
      )
      .catch((e) => console.error("❌ Telegram error:", e.message));
  }
}

let scheduledTask = null;
let currentSchedule = { time: "09:00", enabled: true, sha: null };

// Función nueva: iniciar schedule por usuario
function startScheduledJobForUser(chatId, timeStr) {
  // Detener schedule anterior para este usuario si existe
  if (userSchedules.has(chatId)) {
    const userSched = userSchedules.get(chatId);
    if (userSched.task) {
      userSched.task.stop();
    }
  }

  const m = timeStr.match(/^([01]\d|2[0-3]):([0-5]\d)$/);
  if (!m) {
    console.warn(`❌ Invalid schedule time for user ${chatId}:`, timeStr);
    return;
  }
  
  const [_, hour, minute] = m;
  const cronExpr = `${minute} ${hour} * * *`;
  console.log(`📅 Scheduling cron for user ${chatId}: ${cronExpr}`);
  
  const task = cron.schedule(
    cronExpr,
    async () => {
      try {
        console.log(`📋 Scheduled scrape for user ${chatId}`);
        await triggerScrapeNowForChat(chatId);
        console.log(`✅ Scheduled scrape completed for user ${chatId}`);
      } catch (err) {
        console.error(`❌ Scheduled job error for user ${chatId}:`, err.message);
      }
    },
    { scheduled: true }
  );

  // Guardar en Map de usuarios
  userSchedules.set(chatId, {
    task: task,
    schedule: { time: timeStr, enabled: true }
  });
}

// Función nueva: detener schedule de usuario
function stopScheduledJobForUser(chatId) {
  if (userSchedules.has(chatId)) {
    const userSched = userSchedules.get(chatId);
    if (userSched.task) {
      userSched.task.stop();
      userSched.schedule.enabled = false;
      console.log(`🛑 Scheduled job stopped for user ${chatId}`);
    }
  }
}

// Función original mantenida para compatibilidad (global)
function startScheduledJob(timeStr) {
  if (scheduledTask) {
    scheduledTask.stop();
    scheduledTask = null;
  }
  const m = timeStr.match(/^([01]\d|2[0-3]):([0-5]\d)$/);
  if (!m) {
    console.warn("❌ Invalid schedule time:", timeStr);
    return;
  }
  const [_, hour, minute] = m;
  const cronExpr = `${minute} ${hour} * * *`;
  console.log(`📅 Scheduling cron: ${cronExpr}`);
  scheduledTask = cron.schedule(
    cronExpr,
    async () => {
      try {
        const chatIds = await getAllChatIdsFromRepo();
        console.log("📋 Scheduled scrape for chats:", chatIds);
        for (const chatId of chatIds) {
          await triggerScrapeNowForChat(chatId);
          await new Promise((r) => setTimeout(r, 3000));
        }
        console.log("✅ Scheduled scrape completed");
      } catch (err) {
        console.error("❌ Scheduled job error:", err.message);
      }
    },
    { scheduled: true }
  );
  currentSchedule = { time: timeStr, enabled: true };
}

function stopScheduledJob() {
  if (scheduledTask) {
    scheduledTask.stop();
    scheduledTask = null;
    currentSchedule.enabled = false;
    console.log("🛑 Scheduled job stopped");
  }
}

async function initScheduleOnStart() {
  try {
    const repoSchedule = await getScheduleFromRepo();
    currentSchedule = {
      time: repoSchedule.time || "09:00",
      enabled: repoSchedule.enabled !== false,
      sha: repoSchedule.sha || null,
    };
    if (currentSchedule.enabled) {
      startScheduledJob(currentSchedule.time);
      console.log(`✅ Scheduler started at ${currentSchedule.time}`);
    } else {
      console.log("ℹ️ Scheduler disabled");
    }
  } catch (err) {
    console.error("❌ Schedule init error:", err.message);
  }
}

// ---- REPORT HELPERS ----
function getTodayDate() {
  // Usar fecha local en lugar de UTC para evitar desfases de zona horaria
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function getYesterdayDate() {
  // Usar fecha local en lugar de UTC para evitar desfases de zona horaria
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const year = yesterday.getFullYear();
  const month = String(yesterday.getMonth() + 1).padStart(2, '0');
  const day = String(yesterday.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}


function extractPlaylistId(url) {
  const match = url?.match(/playlist\/([a-zA-Z0-9]+)/);
  return match ? match[1] : "unknown";
}

async function getReportFromGitHub(chatId, date) {
  const pathRepo = `data/${chatId}/${date}.json`;
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${pathRepo}`;

  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 15000,
    });
    console.log("ℹ️ getReportFromGitHub: status", res.status, "path", pathRepo);
    const content = Buffer.from(res.data.content, "base64").toString("utf-8");
    const parsed = JSON.parse(content);
    if (parsed.chatId && String(parsed.chatId) !== String(chatId)) {
      console.warn("⚠️ getReportFromGitHub: chatId mismatch in file", parsed.chatId, "expected", chatId);
      return null;
    }
    return parsed;
  } catch (err) {
    if (err.response) {
      console.log("ici")
      console.warn("⚠️ GitHub report fetch error:", err.response.status, err.response.data?.message || err.response.data || "no message");
      if (err.response.status === 404) return null;
    } else {
      console.error("❌ GitHub report fetch error (no response):", err.message);
    }
    throw err;
  }
}




function formatNumber(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US");
}

function escapeHtml(text) {
  if (!text) return "";
  return text
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function parseStreamsValue(streams) {
  if (!streams) return 0;
  if (typeof streams === "number") return Math.round(streams);
  let str = String(streams).trim().toUpperCase().replace(/[,\s]/g, "");
  const match = str.match(/^([\d.]+)([KMB])?$/);
  if (!match) return parseInt(str.replace(/[^\d]/g, "") || "0", 10);
  let num = parseFloat(match[1]);
  const suffix = match[2];
  if (suffix === "K") num *= 1000;
  else if (suffix === "M") num *= 1000000;
  else if (suffix === "B") num *= 1000000000;
  return Math.round(num);
}



async function generateCountReport(ctx, todayData, yesterdayData, today) {
  const lines = [];
  const playlistSummaries = [];
  let totalPlaylists = (todayData.playlists || []).length;
  let globalStreamsDelta = 0;

  for (const playlist of todayData.playlists || []) {
    const pname = playlist.playlistName || `Playlist ${extractPlaylistId(playlist.url)}`;
    const pTracks = Array.isArray(playlist.tracks) ? playlist.tracks : [];

    const totalStreamsToday =
      playlist.additionalInfo?.totalCalculatedStreams ||
      parseStreamsValue(playlist.totalStreams) ||
      pTracks.reduce((a, t) => a + parseStreamsValue(t.streams), 0);

    console.log(`🔍 Searching for playlist: ${playlist.url}`);
    console.log(`📊 Yesterday data exists:`, !!yesterdayData);
    console.log(`📊 Yesterday playlists count:`, yesterdayData?.playlists?.length || 0);
    
    const yesterdayPlaylist = (yesterdayData?.playlists || []).find(
      (p) => p.url === playlist.url || extractPlaylistId(p.url) === extractPlaylistId(playlist.url)
    );
    
    console.log(`📊 Yesterday playlist found:`, !!yesterdayPlaylist);

    let totalStreamsYesterday = 0;
    if (yesterdayPlaylist) {
      totalStreamsYesterday =
        yesterdayPlaylist.additionalInfo?.totalCalculatedStreams ||
        parseStreamsValue(yesterdayPlaylist.totalStreams) ||
        (yesterdayPlaylist.tracks || []).reduce((a, t) => a + parseStreamsValue(t.streams), 0);
    }

    const playlistStreamsDelta = totalStreamsToday - totalStreamsYesterday;
    globalStreamsDelta += playlistStreamsDelta;

    const yesterdayTracksMap = new Map();
    
    if (yesterdayPlaylist && yesterdayPlaylist.tracks) {
      yesterdayPlaylist.tracks.forEach((track) => {
        if (track?.entityId) {
          yesterdayTracksMap.set(track.entityId, parseStreamsValue(track.streams));
        }
        if (track?.title && track?.artists) {
          const key = `${track.title.toLowerCase().trim()}||${track.artists.join(",").toLowerCase().trim()}`;
          yesterdayTracksMap.set(key, parseStreamsValue(track.streams));
        }
      });
    }

    const totalTracks = Number(playlist.totalTracks || (playlist.tracks || []).length || 0);
    const trackLines = [];

    const topTracks = pTracks.slice(0, 10);
    
    if (!topTracks.length) {
      trackLines.push(`(no tracks extracted)\n`);
    } else {
      for (const track of topTracks) {
        const title = track.title || "Unknown";
        const artists = (track.artists && track.artists.length) ? track.artists.join(", ") : "Unknown Artist";
        const streamsToday = parseStreamsValue(track.streams);
        
        let streamsYesterday = 0;
        let wasPresent = false;

        if (track.entityId && yesterdayTracksMap.has(track.entityId)) {
          streamsYesterday = yesterdayTracksMap.get(track.entityId);
          wasPresent = true;
        } 
        else if (track.title && track.artists) {
          const key = `${track.title.toLowerCase().trim()}||${track.artists.join(",").toLowerCase().trim()}`;
          if (yesterdayTracksMap.has(key)) {
            streamsYesterday = yesterdayTracksMap.get(key);
            wasPresent = true;
          }
        }

        let suffix = "";
        if (wasPresent) {
          const delta = streamsToday - streamsYesterday;
          suffix = ` (${delta >= 0 ? "+" : ""}${formatNumber(delta)} streams)`;
        } else {
          suffix = " (NEW)";
        }

        trackLines.push(`🎯 ${escapeHtml(title)} by ${escapeHtml(artists)}: ${formatNumber(streamsToday)} streams${suffix}`);
      }
    }

    const deltaText = playlistStreamsDelta >= 0 
      ? `📈 +${formatNumber(playlistStreamsDelta)}` 
      : `📉 ${formatNumber(playlistStreamsDelta)}`;

    lines.push(`🎵 Playlist: ${escapeHtml(pname)}`);
    lines.push(`🎧 ${totalTracks} tracks | 🌊 ${playlist.totalStreams} total streams | ${deltaText} since yesterday\n`);
    lines.push(`Track changes:`);
    lines.push(...trackLines);
    lines.push("\n");
    totaleToday=playlist.totalStreams;
    playlistSummaries.push({
      id: extractPlaylistId(playlist.url),
      name: pname,
      totalTracks,
      totalStreamsToday,
      playlistStreamsDelta,
      totaleToday
      
    });
  }

  lines.push("📊 General Report");
  for (const summary of playlistSummaries) {
    const deltaText = summary.playlistStreamsDelta >= 0 
      ? `📈 +${formatNumber(summary.playlistStreamsDelta)}` 
      : `📉 ${formatNumber(summary.playlistStreamsDelta)}`;
      
    lines.push(`🎵 Playlist: ${escapeHtml(summary.name)}`);
    lines.push(`🎧 ${summary.totalTracks} tracks | 🌊 ${summary.totaleToday} total streams | ${deltaText}`);
    //or     lines.push(`🎧 ${summary.totalTracks} tracks |🎤  ${summary.uniqueArtists} unique artiste| 🌊 ${formatNumber(summary.totalStreamsToday)} total streams | ${deltaText}`);

    lines.push("");
  }

  lines.push(`🎵 Total Playlists Processed: ${totalPlaylists}`);
  
  const globalDeltaText = globalStreamsDelta >= 0 
    ? `📈 +${formatNumber(globalStreamsDelta)}` 
    : `📉 ${formatNumber(globalStreamsDelta)}`;
  //lines.push(`🌊 Global Streams Change: ${globalDeltaText}`);
  lines.push(`📅 Report Date: ${today}`);

  const fullReport = lines.join("\n");
  const maxLength = 4000;
  
  if (fullReport.length <= maxLength) {
    await ctx.reply(fullReport, { parse_mode: "HTML" });
  } else {
    const parts = [];
    let currentPart = "";
    
    for (const line of lines) {
      if ((currentPart + line + "\n").length > maxLength) {
        if (currentPart) parts.push(currentPart);
        currentPart = line + "\n";
      } else {
        currentPart += line + "\n";
      }
    }
    if (currentPart) parts.push(currentPart);
    
    for (const part of parts) {
      await ctx.reply(part, { parse_mode: "HTML" });
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }
}

// ---- BOT COMMANDS ----
bot.command("add", async (ctx) => {
  const chatId = ctx.chat.id;
  const args = ctx.message.text.split(" ");
  const newUrl = args[1];
  if (!newUrl || !isValidPlaylistUrl(newUrl)) {
    return ctx.reply(
      `❌ <b>Invalid URL</b>\n\n🔍 <b>Use:</b> <code>/add https://open.spotify.com/playlist/ID</code>\n📝 <b>Example:</b> <code>/add https://open.spotify.com/playlist/0mdKzQBwAutLFCncVSXQPL</code>`,
      { parse_mode: "HTML" }
    );
  }

  try {
    const { urls, sha } = await getPlaylistsFromGitHub(chatId);
    //const normalizedUrl = normalizeUrl(newUrl);
    if (urls.includes(newUrl)) {
      return ctx.reply(
        `⚠️ <b>Playlist already added</b>\n\n📋 Use /list to view playlists`,
        { parse_mode: "HTML" }
      );
    }
    urls.push(newUrl);
    await updatePlaylistsInGitHub(chatId, urls, sha);
    ctx.reply(
      `✅ <b>Playlist added</b>\n\n🎵 URL: ${newUrl}\n📊 Total: ${urls.length} playlist(s)\n🔄 Use /check to analyze`,
      { parse_mode: "HTML" }
    );
  } catch (e) {
    console.error("❌ Add error:", e.message);
    ctx.reply(`❌ <b>Add error</b>\n\n🛠️ Details: ${e.message}`, {
      parse_mode: "HTML",
    });
  }
});

bot.command("remove", async (ctx) => {
  const chatId = ctx.chat.id;
  const args = ctx.message.text.split(" ");
  const urlToRemove = args[1];
  if (!urlToRemove) {
    return ctx.reply(
      `❌ <b>Invalid format</b>\n\n🔍 <b>Use:</b> <code>/remove https://app.artist.tools/playlist/ID</code>\n💡 Use /list to see URLs`,
      { parse_mode: "HTML" }
    );
  }

  try {
    const { urls, sha } = await getPlaylistsFromGitHub(chatId);
    const normalizedUrl = normalizeUrl(urlToRemove);
    const newUrls = urls.filter((u) => u !== normalizedUrl && u !== urlToRemove);
    if (newUrls.length === urls.length) {
      return ctx.reply(
        `❌ <b>URL not found</b>\n\n📋 Use /list to see playlists\n🔍 URL: ${urlToRemove}`,
        { parse_mode: "HTML" }
      );
    }
    await updatePlaylistsInGitHub(chatId, newUrls, sha);
    ctx.reply(
      `🗑️ <b>Playlist removed</b>\n\n❌ URL: ${urlToRemove}\n📊 Remaining: ${newUrls.length}`,
      { parse_mode: "HTML" }
    );
  } catch (e) {
    console.error("❌ Remove error:", e.message);
    ctx.reply(`❌ <b>Remove error</b>\n\n🛠️ Details: ${e.message}`, {
      parse_mode: "HTML",
    });
  }
});

bot.command("list", async (ctx) => {
  const chatId = ctx.chat.id;
  try {
    const { urls } = await getPlaylistsFromGitHub(chatId);
    console.log(urls);
    if (!urls.length) {
      return ctx.reply(
        `📂 <b>No playlists</b>\n\n🚀 <b>Start:</b> <code>/add https://app.artist.tools/playlist/ID</code>`,
        { parse_mode: "HTML" }
      );
    }
    let message = `📋 <b>Your Playlists (${urls.length}):</b>\n\n`;
    urls.forEach((url, i) => {
      const playlistId = extractPlaylistId(url);
      message += `${i + 1}. <b>ID:</b> <code>${playlistId}</code>\n   🔗 ${url}\n\n`;
    });
    message += `🔄 <b>Commands:</b>\n• /check - Analyze\n• /remove URL - Remove\n• /show_schedule - View schedule`;
    ctx.reply(message, { parse_mode: "HTML" });
  } catch (e) {
    console.error("❌ List error:", e.message);
    ctx.reply(`❌ <b>List error</b>\n\n🛠️ Details: ${e.message}`, {
      parse_mode: "HTML",
    });
  }
});

async function scrapeNowHandler(ctx) {
  const chatId = ctx.chat.id;

  // Récupérer rapidement la liste d'URLs pour pouvoir afficher "Analyzing N playlist(s)..."
  let urls = [];
  let normalizedUrl=[];
  try {
    const res = await getPlaylistsFromGitHub(chatId);
    urls = Array.isArray(res.urls) ? res.urls : [];
    
    for(const url of urls){
      normalizedUrl.push(normalizeUrl(url));
    };
    console.log(normalizedUrl);
    urls=normalizedUrl;
  } catch (e) {
    console.warn("⚠️ Failed to fetch playlists before reply:", e && e.message ? e.message : e);
    // si erreur, on laisse urls = []
  }

  // Affiche exactement la phrase demandée (ne pas la retirer)
  try {
    await ctx.reply(`⏳ <b>Analyzing ${urls.length} playlist(s)...</b>`, { parse_mode: "HTML" });
  } catch (e) {
    console.warn("⚠️ Impossible d'envoyer le message initial (non bloquant):", e && e.message ? e.message : e);
  }

  // Lancer le travail long en arrière-plan (fire-and-forget)
  (async () => {
    try {
      // Si pas de playlists -> message d'erreur et stop
      if (!urls || urls.length === 0) {
        try {
          await axios.post(
            `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
            {
              chat_id: chatId,
              text: `📂 <b>No playlists to analyze</b>\n\n🚀 <b>Start:</b> <code>/add https://app.artist.tools/playlist/ID</code>`,
              parse_mode: "HTML",
            },
            { timeout: 15000 }
          );
        } catch (_) {}
        return;
      }

      // Lance le scrapper local (peut durer longtemps)
      await runLocalScraping(chatId);

      const today = getTodayDate();
      const yesterday = getYesterdayDate();

      // Attendre l'upload du rapport — timeout long mais en arrière-plan
      const waitTimeoutMs = 300_000; // 5 minutes
      const pollIntervalMs = 5000; // poll toutes les 5s
      const start = Date.now();
      let todayData = null;

      while (Date.now() - start < waitTimeoutMs) {
        try {
          todayData = await getReportFromGitHub(chatId, today);
          if (todayData) break;
        } catch (e) {
          console.warn("⚠️ GitHub read attempt failed (background):", e && e.message ? e.message : e);
        }
        await new Promise((r) => setTimeout(r, pollIntervalMs));
      }

      if (!todayData) {
        try {
          await axios.post(
            `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
            {
              chat_id: chatId,
              text:
                `⚠️ <b>No report found</b>\n\nLe scrapper a peut-être terminé mais aucun rapport n'a été trouvé sur GitHub pour aujourd'hui (${today}).\n` +
                `Vérifie les logs du scrapper.`,
              parse_mode: "HTML",
            },
            { timeout: 15000 }
          );
        } catch (_) {}
        return;
      }

      // Récupérer yesterdayData pour comparaison (ok si null)
      let yesterdayData = null;
      try {
        yesterdayData = await getReportFromGitHub(chatId, yesterday);
      } catch (e) {
        console.warn("⚠️ Erreur en récupérant les données d'hier (background):", e && e.message ? e.message : e);
      }

      // Générer le rapport et l'envoyer au chat
      try {
        await generateCountReport(ctx, todayData, yesterdayData, today);
      } catch (e) {
        console.warn("⚠️ generateCountReport failed, trying fallback send:", e && e.message ? e.message : e);
        try {
          await axios.post(
            `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
            {
              chat_id: chatId,
              text: `✅ Rapport prêt — mais erreur à l'envoi automatique. Regarde les logs.`,
              parse_mode: "HTML",
            },
            { timeout: 15000 }
          );
        } catch (_) {}
      }
    } catch (err) {
      console.error("❌ Background scrape task failed:", err && err.stack ? err.stack : err);
      try {
        await axios.post(
          `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
          {
            chat_id: chatId,
            text: `❌ <b>Auto-scrape error</b>\n🛠️ Details: ${err && err.message ? err.message : "unknown"}`,
            parse_mode: "HTML",
          },
          { timeout: 15000 }
        );
      } catch (_) {}
    }
  })(); // IIFE — fire and forget

  // Handler retourne immédiatement
}


bot.command("check", scrapeNowHandler);

bot.command("show_schedule", async (ctx) => {
  const chatId = ctx.chat.id;
  try {
    // Priorizar schedule del usuario, fallback al global
    let time, enabled;
    try {
      const userSchedule = await getScheduleFromRepoForUser(chatId);
      time = userSchedule.time;
      enabled = userSchedule.enabled;
    } catch (err) {
      console.log(`ℹ️ No user schedule for ${chatId}, using global`);
      const globalSchedule = await getScheduleFromRepo();
      time = globalSchedule.time;
      enabled = globalSchedule.enabled;
    }

    ctx.reply(
      `📅 <b>Your Schedule:</b> ${time} (${enabled ? "enabled" : "disabled"})\n\nℹ️ Runs /check daily for your playlists`,
      { parse_mode: "HTML" }
    );
  } catch (err) {
    console.error("❌ Show_schedule error:", err.message);
    ctx.reply(`❌ <b>Schedule error</b>\n\n🛠️ Details: ${err.message}`, {
      parse_mode: "HTML",
    });
  }
});

bot.command("set_schedule", async (ctx) => {
  const chatId = ctx.chat.id;
  const args = ctx.message.text.split(" ");
  const time = args[1];
  if (!time || !/^([01]\d|2[0-3]):([0-5]\d)$/.test(time)) {
    return ctx.reply(
      `❌ <b>Invalid format</b>\n\n🔍 <b>Use:</b> <code>/set_schedule HH:MM</code>\n📝 <b>Example:</b> <code>/set_schedule 09:00</code>`,
      { parse_mode: "HTML" }
    );
  }
  try {
    // Usar funciones nuevas para schedule por usuario
    let userSchedule;
    try {
      userSchedule = await getScheduleFromRepoForUser(chatId);
    } catch (err) {
      userSchedule = { time: "09:00", enabled: true, sha: null };
    }
    
    await updateScheduleInRepoForUser(chatId, time, true, userSchedule.sha);
    startScheduledJobForUser(chatId, time);
    
    ctx.reply(`✅ <b>Your schedule set:</b> ${time} (daily)\n\nℹ️ Only affects your playlists`, {
      parse_mode: "HTML",
    });
  } catch (err) {
    console.error(`❌ Set_schedule error for user ${chatId}:`, err.message);
    ctx.reply(`❌ <b>Schedule error</b>\n\n🛠️ Details: ${err.message}`, {
      parse_mode: "HTML",
    });
  }
});

bot.command("disable_schedule", async (ctx) => {
  const chatId = ctx.chat.id;
  try {
    // Desactivar schedule del usuario específico
    let userSchedule;
    try {
      userSchedule = await getScheduleFromRepoForUser(chatId);
    } catch (err) {
      userSchedule = { time: "09:00", enabled: false, sha: null };
    }
    
    await updateScheduleInRepoForUser(chatId, userSchedule.time || "09:00", false, userSchedule.sha);
    stopScheduledJobForUser(chatId);
    
    ctx.reply(
      `🛑 <b>Your schedule disabled</b>\n\nReactivate with <code>/set_schedule HH:MM</code>`,
      { parse_mode: "HTML" }
    );
  } catch (err) {
    console.error(`❌ Disable_schedule error for user ${chatId}:`, err.message);
    ctx.reply(`❌ <b>Disable error</b>\n\n🛠️ Details: ${err.message}`, {
      parse_mode: "HTML",
    });
  }
});

bot.command("help", (ctx) => {
  ctx.reply(
    `🤖 <b>Spotydaily Bot</b>\n\n📋 <b>Commands:</b>\n\n` +
      `🔗 <b>/add [URL]</b> - Add playlist\n` +
      `🗑️  <b>/remove [URL]</b> - Remove playlist\n` +
      `📋 <b>/list</b> - List playlists\n` +
      `🚀 <b>/check</b> - Analyze now\n` +
      `💁‍ <b>/show_schedule</b> - View schedule\n` +
      `⏰ <b>/set_schedule HH:MM</b> - Set daily schedule\n` +
      `🛑 <b>/disable_schedule</b> - Disable schedule\n` +
      `💡 <b>/help</b> - Show help`,
    { parse_mode: "HTML" }
  );
});

bot.start((ctx) => {
  const chatId = ctx.chat.id;
  ctx.reply(
    `🎵 <b>🤖 Welcome! Here begins your daily stream count.</b>\n\n` +
      `👋 <b>Chat ID:</b> <code>${chatId}</code>\n` +
      `🚀 <b>Start:</b> <code>/add https://open.spotify.com/playlist/ID</code>\n` +
      `💡 <b>Help:</b> <code>/help</code>`,
    { parse_mode: "HTML" }
  );
});

bot.catch((err, ctx) => {
  console.error("❌ Bot error:", err.stack);
  ctx.reply(
    `❌ <b>Bot error</b>\n\n🛠️ Details: ${err.message}\n🔄 Retry later`,
    { parse_mode: "HTML" }
  );
});

// ---- START BOT ----
// ---- START BOT ----
bot
  .launch()
  .then(() => {
    console.log("🤖 Bot started");
    // Ne pas await : lance l'init async en arrière-plan pour éviter timeout de launch
    initScheduleOnStart().catch((err) => {
      console.error("❌ initScheduleOnStart failed (background):", err && err.stack ? err.stack : err);
    });
  })
  .catch((error) => {
    console.error("❌ Bot start error:", error && error.stack ? error.stack : error);
    process.exit(1);
  });


process.once("SIGINT", () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
