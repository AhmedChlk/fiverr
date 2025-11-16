const { chromium } = require("playwright");
const axios = require("axios");
const { Worker } = require("worker_threads");
const os = require("os");
const fs = require("fs");
const pathModule = require("path"); 

// ====== CONFIGURATION ======
const TELEGRAM_BOT_TOKEN = ":";
const GITHUB_TOKEN = "ghp_";
const GITHUB_REPO_OWNER = "-";
const GITHUB_REPO_NAME = "--";
const CHAT_ID = process.env.TELEGRAM_CHAT_ID || process.env.CHAT_ID || "";

// Nombre de threads basé sur les CPU disponibles
const MAX_THREADS = Math.min(os.cpus().length, 4);

// ---- FILE PATHS ----
function pathForChatPlaylists(chatId) {
  return `playlist-data/${chatId}/urls.txt`;
}
function pathForChatData(chatId, date) {
  return `data/${chatId}/${date}.json`;
}

// ---- HELPER FUNCTIONS ----
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

function formatNumber(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US");
}

async function sendToTelegram(text) {
  try {
    await axios.post(
      `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
      {
        chat_id: CHAT_ID,
        text: text.slice(0, 4096),
        parse_mode: "HTML",
      },
      { timeout: 10000 }
    );
    console.log("✅ Sent Telegram message");
  } catch (error) {
    console.error("❌ Telegram send error:", error.message);
  }
}

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

async function getPlaylistsFromGitHub(chatId) {
  const path = pathForChatPlaylists(chatId);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });

    const content = Buffer.from(res.data.content, "base64").toString("utf-8");

    // split + trim + enlever commentaires/vides
    const lines = content
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"));

    // normaliser artist.tools -> app.artist.tools (faire ça AVANT le filter)
    const normalized = lines.map((line) =>
      line.replace("https://artist.tools", "https://app.artist.tools")
    );

    // garder seulement les URLs valides (ex: app.artist.tools et open.spotify.com/playlist/...)
    const urls = normalized.filter((line) => isValidPlaylistUrl(line));

    // extraire domaines uniques
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

    console.log(`📋 Found ${urls.length} valid playlist URLs`);
    return urls;
  } catch (error) {
    // gérer proprement le 404 (retourne objet vide comme dans la version précédente)
    if (error.response && error.response.status === 404) {
      console.warn(`⚠️ getPlaylistsFromGitHub: ${path} not found (404). Returning empty list.`);
      return { urls: [], content: "", sha: null, path, domains: [] };
    }
    console.error("❌ GitHub fetch error:", error.message);
    throw error;
  }
}


async function getDataFromGitHub(chatId, date) {
  const path = pathForChatData(chatId, date);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;
  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 10000,
    });
    return JSON.parse(
      Buffer.from(res.data.content, "base64").toString("utf-8")
    );
  } catch (error) {
    if (error.response?.status === 404) return null;
    console.error("❌ GitHub data fetch error:", error.message);
    throw error;
  }
}

async function saveDataToGitHub(chatId, date, data) {
  const path = `data/${chatId}/${date}.json`;
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;
  const payload = {
    message: `Daily playlist report for ${chatId} - ${date}`,
    content: Buffer.from(JSON.stringify(data, null, 2)).toString("base64"),
  };

  try {
    let existingSha = null;
    try {
      const existing = await axios.get(url, {
        headers: { Authorization: `token ${GITHUB_TOKEN}` },
        timeout: 10000,
      });
      existingSha = existing.data?.sha || null;
      console.log("ℹ️ Found existing file on GitHub, sha=", existingSha);
    } catch (e) {
      if (e.response?.status === 404) {
        console.log("ℹ️ No existing file on GitHub, will create new.");
      } else {
        console.warn("⚠️ Error checking existing file:", e.response?.status, e.message);
      }
    }
    if (existingSha) payload.sha = existingSha;

    const res = await axios.put(url, payload, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
      timeout: 20000,
    });

    console.log("✅ saveDataToGitHub response status:", res.status);
    console.log("✅ saved to path:", res.data?.content?.path, "sha:", res.data?.content?.sha);
    return { ok: true, sha: res.data?.content?.sha || null };
  } catch (err) {
    console.error("❌ saveDataToGitHub failed:", err.response?.status, err.response?.data || err.message);
    try {
      const dbgPath = pathModule.join(__dirname, "data", String(chatId), `${date}.failed.json`);
     // await fs.promises.mkdir(pathModule.dirname(dbgPath), { recursive: true });
      //await fs.promises.writeFile(dbgPath, JSON.stringify({ error: err.toString(), payloadSize: payload.content.length }, null, 2));
      console.log("ℹ️ Wrote failure debug file:", dbgPath);
    } catch (e) {
      console.error("⚠️ failed to write debug file:", e.message);
    }
    throw err;
  }
}

function extractPlaylistId(url) {
  const match = url.match(/playlist\/([a-zA-Z0-9]+)/);
  return match ? match[1] : "Unknown";
}

// ---- WORKER THREAD SCRIPT ----
const workerScript = `
const { parentPort } = require('worker_threads');
const { chromium } = require("playwright");

async function scrapePlaylist(url) {
  let browser = null;
  try {
    browser = await chromium.launch({
      headless: true, 
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage", 
        "--disable-gpu", 
      ],
    });
    const context = await browser.newContext({
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
      viewport: { width: 1280, height: 720 },
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();

    console.log(\`🔍 Worker scraping: \${url}\`);
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await page.goto(url, {
          waitUntil: "domcontentloaded",
          timeout: 50000,
        });
        break;
      } catch (navError) {
        console.log(
          \`⚠️ Navigation attempt \${attempt} failed: \${navError.message}\`
        );
        if (attempt === 3)
          throw new Error("Failed navigation after 3 attempts");
        await new Promise((resolve) => setTimeout(resolve, 20000));
      }
    }

    await Promise.race([
      page.waitForSelector("h1", { timeout: 100000 }),
      page.waitForSelector('[class*="playlist"]', { timeout: 100000 }),
      page.waitForTimeout(120000),
    ]).catch((err) => console.log("⚠️ Selector wait:", err.message));

    await page.waitForTimeout(10000);

    const initialPlaylistData = await page.evaluate(() => {
      function getText(selector) {
        const el = document.querySelector(selector);
        return el ? el.textContent.trim() : "";
      }
      let playlistName = "Unknown Playlist";
      const breadcrumbSelectors = [
        'nav[aria-label="breadcrumb"] span:last-child',
        ".breadcrumb span:last-child",
      ];
      for (const sel of breadcrumbSelectors) {
        const name = getText(sel);
        if (name && !name.toLowerCase().includes("artist.tools")) {
          playlistName = name;
          break;
        }
      }
      if (playlistName === "Unknown Playlist") {
        const title = document.title.split(" | ")[0].split(" - ")[0].trim();
        if (
          title &&
          !title.toLowerCase().includes("artist.tools") &&
          title.length > 3
        ) {
          playlistName = title;
        }
      }

      let totalTracks = 0,
        uniqueArtists = 0,
        latestTrack = "";
      const statElements = document.querySelectorAll(
        ".text-2xl, .font-mono, [class*='stat']"
      );
      statElements.forEach((el) => {
        const value = el.textContent.trim();
        const desc =
          el.parentElement
            ?.querySelector(".text-sm, [class*='description']")
            ?.textContent.trim()
            .toLowerCase() || "";
        const num = parseInt(value.replace(/[^0-9]/g, "")) || 0;
        if (desc.includes("total") && desc.includes("track")) totalTracks = num;
        else if (desc.includes("unique") && desc.includes("artist"))
          uniqueArtists = num;
        else if (desc.includes("latest") && desc.includes("track"))
          latestTrack = value;
      });

      return { playlistName, totalTracks, uniqueArtists, latestTrack };
    });

    const trackButtonSelectors = [
      'a[href*="tracks"]',
      'button:has-text("Tracks")',
      '[data-testid="tracks-tab"]',
      'a[aria-label="Tracks"]',
      'a[href*="songs"]',
    ];
    let tracksButton = null;
    for (const sel of trackButtonSelectors) {
      tracksButton = await page.$(sel);
      if (tracksButton) break;
    }

    // On initialise les variables qui vont contenir les nouvelles données
    let totalStreamsValue = "N/A";
    let trackCountValue = 0;
    let avgPopularityValue = "N/A";
    let popularityRangeValue = "N/A";
    let tracks = [];

    if (tracksButton) {
      await tracksButton.scrollIntoViewIfNeeded();
      await tracksButton.click();
      await page.waitForTimeout(15000);

      try {
        const [streams, trackCount, avgPop, popRange] = await Promise.all([
          page.$eval('div:has(div.text-xs:text("Total Streams")) > div.font-medium', el => el.textContent.trim()).catch(() => "N/A"),
          page.$eval('div:has(div.text-xs:text("Track Count")) > div.font-medium', el => el.textContent.trim()).catch(() => "0"),
          page.$eval('div:has(div.text-xs:text("Avg Popularity")) > div.font-medium', el => el.textContent.trim()).catch(() => "N/A"),
          page.$eval('div:has(div.text-xs:text("Popularity Range")) > div.font-medium', el => el.textContent.trim()).catch(() => "N/A")
        ]);

        totalStreamsValue = streams;
        trackCountValue = parseInt(trackCount.replace(/[^0-9]/g, ""), 10) || 0;
        avgPopularityValue = avgPop;
        popularityRangeValue = popRange;
        
      } catch (e) {
        console.log("⚠️ L'extraction des statistiques a échoué:", e.message);
      }
      
      try {
        const perPageButton = page.locator('button[role="combobox"]:has-text("per page")');
        if (await perPageButton.isVisible()) {
          console.log("🖱️ Clicking 'per page' dropdown...");
          await perPageButton.click();
          
          const option100 = page.locator('[role="option"]:has-text("1000 per page")');
          await option100.waitFor({ state: 'visible', timeout: 5000 });
          await option100.click();
          console.log("✅ Selected '100 per page'. Waiting for tracks to load...");

          await page.waitForLoadState('networkidle', { timeout: 60000 });
        }
      } catch (e) {
        console.log("⚠️ Could not change items per page:", e.message);
      }

      const gridButton = await page.$('button[aria-label="Grid view"]');
      if (gridButton) {
        await gridButton.click();
        await page
          .waitForSelector("div[data-entity-card]", { timeout: 120000 })
          .catch(() => {});
        await page
          .waitForFunction(
            () => {
              const els = document.querySelectorAll("div[data-entity-card]");
              window.__cardStability = window.__cardStability || {
                lastCount: 0,
                stableSince: Date.now(),
              };
              if (els.length !== window.__cardStability.lastCount) {
                window.__cardStability = {
                  lastCount: els.length,
                  stableSince: Date.now(),
                };
                return false;
              }
              return Date.now() - window.__cardStability.stableSince > 1000;
            },
            { timeout: 60000 }
          )
          .catch(() => console.log("⚠️ Card stabilization timeout"));
      }

      tracks = await page.evaluate(() => {
        return Array.from(document.querySelectorAll("div[data-entity-card]"))
          .map((node) => {
            const entityId = node.getAttribute("data-entity-card") || null;
            const title =
              node.querySelector("h3")?.textContent.trim() || "Unknown Track";
            const artists = Array.from(
              node.querySelectorAll('a[href^="/artist/"]')
            )
              .map((a) => a.textContent.trim())
              .filter(Boolean);
            let streams = null;
            const monoSpans = node.querySelectorAll("span.font-mono.text-sm");
            for (const s of monoSpans) {
              const txt = s.textContent.replace(/\\u202F/g, " ").trim();
              if (/\\d/.test(txt)) {
                streams = parseInt(txt.replace(/[^\\d]/g, "") || "0", 10);
                break;
              }
            }
            return { entityId, title, artists, streams };
          })
          .filter((t) => t.title !== "Unknown Track" || t.streams !== null);
      });
    }
    
    const totalCalculatedStreams = tracks.reduce((accumulator, currentTrack) => {
      return accumulator + (currentTrack.streams || 0);
    }, 0); 

    const result = {
      ...initialPlaylistData,
      tracks,
      totalTracks: trackCountValue || initialPlaylistData.totalTracks || tracks.length,
      totalStreams: totalStreamsValue,
      url,
      additionalInfo: {
        avgPopularity: avgPopularityValue,
        popularityRange: popularityRangeValue,
        uniqueArtists: initialPlaylistData.uniqueArtists || tracks.length,
        latestTrack: initialPlaylistData.latestTrack || tracks[0]?.title || "",
        totalCalculatedStreams: totalCalculatedStreams,
        extractionTimestamp: new Date().toISOString(),
        extractionMethod: tracks.length > 0 ? "success" : "minimal",
      },
    };

    await context.close();
    return result;
  } catch (error) {
    console.error(\`❌ Worker scrape error for \${url}:\`, error.message);
    return {
      playlistName: \`Error - \${url.match(/playlist\\/([a-zA-Z0-9]+)/) ? url.match(/playlist\\/([a-zA-Z0-9]+)/)[1] : "Unknown"}\`,
      tracks: [],
      totalTracks: 0,
      totalStreams: "N/A",
      url,
      error: error.message,
      additionalInfo: {
        uniqueArtists: 0,
        latestTrack: "",
        extractionTimestamp: new Date().toISOString(),
        extractionMethod: "error",
      },
    };
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (e) {
        console.error("❌ Browser close error:", e.message);
      }
    }
  }
}

parentPort.on('message', async (url) => {
  try {
    const result = await scrapePlaylist(url);
    parentPort.postMessage({ success: true, data: result });
  } catch (error) {
    parentPort.postMessage({ success: false, error: error.message, url });
  }
});
`;

// ---- MULTI-THREAD SCRAPING ----
async function scrapePlaylistsMultiThread(urls) {
  const tryMultiThread = () => new Promise((resolve, reject) => {
    const results = [];
    let completedCount = 0;
    const workers = [];
    const urlQueue = [...urls];
    let resolved = false;
    console.log(`🚀 Attempting multi-thread: ${Math.min(MAX_THREADS, urls.length)} workers for ${urls.length} URLs`);

    const cleanup = () => {
      workers.forEach((w, index) => {
        if (w) {
          try {
            console.log(`🧹 Terminating worker ${index}`);
            w.terminate();
          } catch(e) {
            console.error(`❌ Error terminating worker ${index}:`, e.message);
          }
        }
      });
    };

    const handleWorkerComplete = (workerId, result) => {
      if (resolved) return; 
      
      completedCount++;
      results.push(result);
      console.log(`✅ Worker ${workerId} completed (${completedCount}/${urls.length})`);
      
      if (urlQueue.length > 0) {
        const nextUrl = urlQueue.shift();
        console.log(`🔄 Worker ${workerId} processing next URL: ${extractPlaylistId(nextUrl)}`);
        try {
          if (workers[workerId] && !resolved) {
            workers[workerId].postMessage(nextUrl);
          }
        } catch (e) {
          console.error(`❌ Error sending message to worker ${workerId}:`, e.message);
        }
      } else if (completedCount === urls.length && !resolved) {
        resolved = true;
        console.log("🏁 All workers completed (multi-thread).");
        cleanup();
        resolve(results);
      }
    };

    try {
      const workerCount = Math.min(MAX_THREADS, urls.length);
      for (let i = 0; i < workerCount; i++) {
        try {
          const worker = new Worker(workerScript, { eval: true });
          workers[i] = worker;

          worker.on('message', (message) => {
            if (resolved) return;
            if (message.success) {
              handleWorkerComplete(i, message.data);
            } else {
              console.error(`❌ Worker ${i} message error:`, message.error || message);
              handleWorkerComplete(i, {
                playlistName: `Error - ${extractPlaylistId(message.url||'unknown')}`,
                tracks: [],
                totalTracks: 0,
                totalStreams: "N/A",
                url: message.url || "unknown",
                error: message.error || "worker message failure",
                additionalInfo: { extractionMethod: "error", extractionTimestamp: new Date().toISOString() }
              });
            }
          });

          worker.on('error', (err) => {
            if (resolved) return;
            console.error(`❌ Worker ${i} thread error:`, err.message);
            handleWorkerComplete(i, {
              playlistName: `Error - Worker ${i}`,
              tracks: [], totalTracks: 0, totalStreams: "N/A", url: "unknown", error: err.message,
              additionalInfo: { extractionMethod: "error", extractionTimestamp: new Date().toISOString() }
            });
          });

          worker.on('messageerror', (err) => {
            console.error(`❌ Worker ${i} messageerror:`, err.message);
          });

          worker.on('exit', (code) => {
            console.log(`ℹ️ Worker ${i} exited with code ${code}`);
          });

          if (urlQueue.length > 0) {
            const url = urlQueue.shift();
            console.log(`🔄 Worker ${i} starting with: ${extractPlaylistId(url)}`);
            worker.postMessage(url);
          }
        } catch (errWorkerCreate) {
          console.error(`❌ Failed to create Worker ${i}:`, errWorkerCreate.message);
        }
      }

      const timeout = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          console.error("❌ Multi-thread timeout reached, aborting multi-thread attempt");
          cleanup();
          reject(new Error("Multi-thread processing timeout"));
        }
      }, 10 * 60 * 1000);

    } catch (err) {
      console.error("❌ Multi-thread startup error:", err.message);
      if (!resolved) {
        resolved = true;
        reject(err);
      }
    }
  });

  const fallbackSequential = async () => {
    console.log("↪️ Falling back to sequential processing (one worker per URL)");
    const results = [];
    for (const url of urls) {
      console.log(`🔁 Sequential worker for: ${extractPlaylistId(url)}`);
      try {
        const res = await new Promise((resolve, reject) => {
          const w = new Worker(workerScript, { eval: true });
          const t = setTimeout(() => {
            try { w.terminate(); } catch(e) {}
            reject(new Error("Worker sequential timeout"));
          }, 5 * 60 * 1000); 

          w.on('message', (m) => {
            clearTimeout(t);
            if (m.success) resolve(m.data);
            else resolve({
              playlistName: `Error - ${extractPlaylistId(m.url||url)}`,
              tracks: [], totalTracks: 0, totalStreams: "N/A", url: m.url||url, error: m.error || "worker error",
              additionalInfo: { extractionMethod: "error", extractionTimestamp: new Date().toISOString() }
            });
            try { w.terminate(); } catch(e) {}
          });
          w.on('error', (err) => {
            clearTimeout(t);
            console.error("❌ Sequential worker error:", err);
            try { w.terminate(); } catch(e) {}
            resolve({
              playlistName: `Error - ${extractPlaylistId(url)}`,
              tracks: [], totalTracks: 0, totalStreams: "N/A", url, error: err.message,
              additionalInfo: { extractionMethod: "error", extractionTimestamp: new Date().toISOString() }
            });
          });
          w.postMessage(url);
        });
        results.push(res);
      } catch (e) {
        console.error("❌ Sequential processing failure for url:", url, e.message);
        results.push({
          playlistName: `Error - ${extractPlaylistId(url)}`,
          tracks: [], totalTracks: 0, totalStreams: "N/A", url, error: e.message,
          additionalInfo: { extractionMethod: "error", extractionTimestamp: new Date().toISOString() }
        });
      }
    }
    return results;
  };

  try {
    return await tryMultiThread();
  } catch (err) {
    console.warn("⚠️ Multi-thread failed, switching to fallback:", err.message);
    return await fallbackSequential();
  }
}

// ---- MAIN EXECUTION ----
(async () => {
  console.log("🚀 Starting multi-threaded scraper...");
  
  if (!TELEGRAM_BOT_TOKEN || !GITHUB_TOKEN || !CHAT_ID) {
    const errorMsg = "❌ Missing credentials";
    console.error(errorMsg);
    await sendToTelegram("❌ <b>Scraper Error</b>: Missing credentials");
    console.log("🔚 Scraper exiting with error code 1");
    process.exit(1);
  }

  let urls = [];
  let normalizedUrl=[];
  try {
    urls = await getPlaylistsFromGitHub(CHAT_ID);
    console.log(urls);
    if (!urls.length) {
      await sendToTelegram("📂 <b>No playlists found</b>\\nUse /add to add playlists");
      console.log("🔚 Scraper exiting normally - no playlists found");
      process.exit(0);
    }
    for(const url of urls){
      normalizedUrl.push(normalizeUrl(url));
    };
    urls=normalizedUrl;
    console.log(urls);
  } catch (e) {
    console.error("❌ Playlist fetch error:", e.message);
    await sendToTelegram(`❌ <b>Error fetching playlists</b>: ${e.message}`);
    console.log("🔚 Scraper exiting with error code 1");
    process.exit(1);
  }

  const today = getTodayDate();
  const yesterday = getYesterdayDate();
  const todayData = {
    date: today,
    chatId: CHAT_ID,
    totalPlaylists: urls.length,
    playlists: [],
    generatedAt: new Date().toISOString(),
  };
  let yesterdayData = await getDataFromGitHub(CHAT_ID, yesterday).catch(() => null);

  console.log(`🔄 Processing ${urls.length} playlists with ${MAX_THREADS} threads...`);
  
  try {
    const playlistResults = await scrapePlaylistsMultiThread(urls);
    
    const enrichedPlaylists = playlistResults.map((playlist) => {
      const tracks = Array.isArray(playlist.tracks) ? playlist.tracks : [];
      const trackCount = playlist.totalTracks || tracks.length || 0;
      let totalStreamsNumeric =
        parseStreamsValue(playlist.totalStreams) ||
        tracks.reduce(
          (acc, track) => acc + parseStreamsValue(track?.streams || 0),
          0
        );

      return {
        ...playlist,
        totalTracks: trackCount,
        additionalInfo: {
          ...(playlist.additionalInfo || {}),
          stats: {
            ...(playlist.additionalInfo?.stats || {}),
            trackCountNumeric: trackCount,
            totalStreamsNumeric: totalStreamsNumeric,
          },
          extractionTimestamp: new Date().toISOString(),
          extractionMethod: tracks.length > 0 ? "success" : "minimal",
        },
      };
    });

    todayData.playlists = enrichedPlaylists;

    console.log("💾 Saving data to GitHub...");
    await saveDataToGitHub(CHAT_ID, today, todayData);
    console.log("✅ Data saved successfully to GitHub");

    let successCount = 0, errorCount = 0;
    enrichedPlaylists.forEach(playlist => {
      playlist.error ? errorCount++ : successCount++;
    });

    const finalMessage = `✅ Multi-threaded scraping completed: ${successCount} success, ${errorCount} errors`;
    console.log(finalMessage);
    
    console.log("🔚 Scraper completed successfully");
    process.exit(0); 
    
  } catch (error) {
    console.error("❌ Multi-thread processing error:", error.message);
    await sendToTelegram(
      `❌ <b>Multi-thread Error</b>\\n🛠️ Error: ${error.message}\\n🔄 Retry later`
    );
    console.log("🔚 Scraper exiting with error code 1");
    process.exit(1); 
  }
})().catch(async (error) => {
  console.error("❌ Fatal error:", error.message);
  await sendToTelegram(
    `❌ <b>Fatal Scraper Error</b>\\n🛠️ Error: ${error.message}\\n🔄 Retry later`
  );
  console.log("🔚 Scraper exiting with fatal error code 1");
  process.exit(1); 
});
