const { chromium } = require("playwright");
const axios = require("axios");

// ====== CONFIG DIRECTA (reemplaza con tus valores) ======
const TELEGRAM_BOT_TOKEN = "";
const GITHUB_TOKEN = "";
const GITHUB_REPO_OWNER = "56565-maker";
const GITHUB_REPO_NAME = "artist-tools-scraper";
// ========================================================

// El bot pasa este valor por env al ejecutar scrape.js
const CHAT_ID =
  process.env.TELEGRAM_CHAT_ID ||
  process.env.CHAT_ID ||
  "AQUI_TU_CHAT_ID_FALLBACK";

// ---- Helpers ----
function pathForChatPlaylists(chatId) {
  return `playlists/${chatId}.txt`;
}

function pathForChatData(chatId, date) {
  return `data/${chatId}/${date}.json`;
}

function getTodayDate() {
  return new Date().toISOString().split("T")[0]; // YYYY-MM-DD
}

function getYesterdayDate() {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  return yesterday.toISOString().split("T")[0]; // YYYY-MM-DD
}

async function sendToTelegram(text) {
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  await axios.post(url, {
    chat_id: CHAT_ID,
    text,
    parse_mode: "HTML",
  });
}

async function getPlaylistsFromGitHub(chatId) {
  const path = pathForChatPlaylists(chatId);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;
  const res = await axios.get(url, {
    headers: { Authorization: `token ${GITHUB_TOKEN}` },
  });
  const content = Buffer.from(res.data.content, "base64").toString("utf-8");
  return content
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

async function getDataFromGitHub(chatId, date) {
  const path = pathForChatData(chatId, date);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;

  try {
    const res = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
    });
    const content = Buffer.from(res.data.content, "base64").toString("utf-8");
    return JSON.parse(content);
  } catch (error) {
    if (error.response && error.response.status === 404) {
      return null; // No existe el archivo
    }
    throw error;
  }
}

async function saveDataToGitHub(chatId, date, data) {
  const path = pathForChatData(chatId, date);
  const url = `https://api.github.com/repos/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/contents/${path}`;

  const payload = {
    message: `Update data for ${chatId} on ${date}`,
    content: Buffer.from(JSON.stringify(data, null, 2)).toString("base64"),
  };

  try {
    // Intentar obtener el archivo existente para obtener el SHA
    const existingRes = await axios.get(url, {
      headers: { Authorization: `token ${GITHUB_TOKEN}` },
    });
    payload.sha = existingRes.data.sha;
  } catch (error) {
    // Si no existe, no pasa nada, se creará nuevo
  }

  await axios.put(url, payload, {
    headers: { Authorization: `token ${GITHUB_TOKEN}` },
  });
}

function parseStreams(streamsText) {
  // Convertir "4,238" a 4238, "3.5K" a 3500, etc.
  const text = streamsText.replace(/,/g, "");

  if (text.includes("K")) {
    return Math.round(parseFloat(text.replace("K", "")) * 1000);
  } else if (text.includes("M")) {
    return Math.round(parseFloat(text.replace("M", "")) * 1000000);
  } else if (text.includes("B")) {
    return Math.round(parseFloat(text.replace("B", "")) * 1000000000);
  } else {
    return parseInt(text) || 0;
  }
}

function formatStreams(number) {
  if (number >= 1000000000) {
    return (number / 1000000000).toFixed(1) + "B";
  } else if (number >= 1000000) {
    return (number / 1000000).toFixed(1) + "M";
  } else if (number >= 1000) {
    return (number / 1000).toFixed(1) + "K";
  } else {
    return number.toString();
  }
}

function extractPlaylistId(url) {
  // Extraer el ID de la playlist de la URL
  const match = url.match(/playlist\/([a-zA-Z0-9]+)/);
  return match ? match[1] : "Unknown";
}

async function scrapePlaylist(page, url) {
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });

    // Esperar a que cargue el contenido inicial
    await page.waitForTimeout(3000);

    // Verificar si hay un error en la página
    const errorElement = await page.$("text=Something went wrong");
    if (errorElement) {
      console.log(`Error en la página: ${url}`);
      return {
        playlistName: "Error",
        artist: "Error",
        tracks: [],
        totalTracks: 0,
        totalStreams: "N/A",
        url: url,
        error: "Página con error del servidor",
      };
    }

    // Hacer clic en el botón Tracks usando múltiples selectores
    try {
      // Intentar con el selector de enlace primero
      const tracksLink = await page.$('a[href$="tracks"]');
      if (tracksLink) {
        await tracksLink.click();
        console.log("Clicked Tracks link");
      } else {
        // Buscar por texto si no funciona el selector
        await page.evaluate(() => {
          const elements = [...document.querySelectorAll("*")];
          const tracksElement = elements.find(
            (el) =>
              el.textContent &&
              el.textContent.trim() === "Tracks" &&
              (el.tagName === "A" ||
                el.tagName === "BUTTON" ||
                el.onclick ||
                el.href)
          );
          if (tracksElement) {
            tracksElement.click();
          }
        });
        console.log("Clicked Tracks element by text");
      }
      await page.waitForTimeout(2000);
    } catch (error) {
      console.log("No se pudo hacer clic en Tracks, continuando...");
    }

    // Hacer clic en Grid view usando el selector correcto
    try {
      await page.waitForSelector('button[aria-label="Grid view"]', {
        timeout: 10000,
      });
      await page.click('button[aria-label="Grid view"]');
      console.log("Clicked Grid view button");
      await page.waitForTimeout(3000);
    } catch (error) {
      console.log("No se pudo hacer clic en Grid view, continuando...");
    }

    // Extraer información de la playlist
    const playlistData = await page.evaluate(() => {
      const tracks = [];

      // Obtener el nombre de la playlist
      const playlistName =
        document.querySelector("h1")?.textContent?.trim() ||
        document.title?.split("|")[0]?.trim() ||
        "Unknown Playlist";

      // Obtener estadísticas de la playlist
      let totalStreams = "N/A";
      let totalTracks = 0;

      const pageText = document.body.innerText;

      // Buscar estadísticas en la página
      const allElements = Array.from(document.querySelectorAll("*"));

      // Buscar Total Streams
      const streamsElement = allElements.find(
        (el) =>
          el.textContent &&
          el.textContent.includes("Total Streams") &&
          el.parentElement &&
          el.parentElement.textContent
      );
      if (streamsElement) {
        const parent = streamsElement.parentElement;
        const match = parent.textContent.match(
          /(\d+(?:\.\d+)?[KMB]?)\s*Total Streams/
        );
        if (match) {
          totalStreams = match[1];
        }
      }

      // Buscar Track Count
      const trackCountElement = allElements.find(
        (el) =>
          el.textContent &&
          el.textContent.includes("Track Count") &&
          el.parentElement &&
          el.parentElement.textContent
      );
      if (trackCountElement) {
        const parent = trackCountElement.parentElement;
        const match = parent.textContent.match(/(\d+)\s*Track Count/);
        if (match) {
          totalTracks = parseInt(match[1]);
        }
      }

      // Extraer canciones usando los selectores correctos
      const songCards = document.querySelectorAll("div[data-entity-card]");

      if (songCards.length > 0) {
        console.log(`Found ${songCards.length} song cards`);

        songCards.forEach((card, index) => {
          // Usar el selector correcto para el nombre de la canción
          const songElement = card.querySelector(
            "h3.font-semibold.text-gray-900.dark\\:text-white.truncate.max-w-full.hover\\:underline.leading-none.py-0\\.5"
          );

          if (songElement) {
            const songName = songElement.textContent.trim();
            const cardText = card.textContent;

            // Extraer artistas usando regex mejorado
            let artists = "";
            const artistsMatch = cardText.match(/Artists:([^S]+?)Streams:/);
            if (artistsMatch) {
              artists = artistsMatch[1].trim();
            }

            // Extraer streams usando regex mejorado
            let streams = "";
            const streamsMatch = cardText.match(/Streams:([\d,]+)/);
            if (streamsMatch) {
              streams = streamsMatch[1];
            }

            // Extraer posición
            let position = index + 1;
            const positionMatch = cardText.match(/Position:(\d+)/);
            if (positionMatch) {
              position = parseInt(positionMatch[1]);
            }

            // Solo agregar si tenemos datos válidos
            if (songName && artists && streams) {
              tracks.push({
                name: songName,
                artist: artists,
                streams: streams,
                position: position,
                index: position,
              });
            }
          }
        });
      }

      // Ordenar por posición y limitar a 10 canciones
      tracks.sort((a, b) => a.position - b.position);
      const limitedTracks = tracks.slice(0, 10);

      // Calcular estadísticas basadas en los datos extraídos
      const actualTotalTracks = limitedTracks.length;
      let calculatedTotalStreams = 0;

      limitedTracks.forEach((track) => {
        const streamCount = parseInt(track.streams.replace(/,/g, ""));
        if (!isNaN(streamCount)) {
          calculatedTotalStreams += streamCount;
        }
      });

      // Usar las estadísticas calculadas si no tenemos las de la página
      const finalTotalTracks =
        totalTracks > 0 ? totalTracks : actualTotalTracks;
      const finalTotalStreams =
        totalStreams !== "N/A"
          ? totalStreams
          : calculatedTotalStreams.toLocaleString();

      return {
        playlistName: playlistName,
        artist: playlistName, // Para playlists, el "artista" es el nombre de la playlist
        tracks: limitedTracks,
        totalTracks: finalTotalTracks,
        totalStreams: finalTotalStreams,
        url: window.location.href,
      };
    });

    return playlistData;
  } catch (error) {
    console.error(`Error scraping ${url}:`, error);
    return {
      playlistName: "Error",
      artist: "Error",
      tracks: [],
      totalTracks: 0,
      totalStreams: "N/A",
      url: url,
      error: error.message,
    };
  }
}

function compareData(todayData, yesterdayData) {
  const changes = [];

  // Si no hay datos de ayer, todas las canciones son nuevas
  if (
    !yesterdayData ||
    !yesterdayData.tracks ||
    yesterdayData.tracks.length === 0
  ) {
    todayData.tracks.forEach((track) => {
      changes.push({
        type: "new",
        track: track,
        change: "nueva",
      });
    });
    return changes;
  }

  // Crear mapa de canciones de ayer usando nombre + artista como clave única
  const yesterdayTracks = new Map();
  yesterdayData.tracks.forEach((track) => {
    const key = `${track.name.toLowerCase().trim()}|${track.artist
      .toLowerCase()
      .trim()}`;
    yesterdayTracks.set(key, track);
  });

  // Comparar cada canción de hoy con ayer
  todayData.tracks.forEach((todayTrack) => {
    const key = `${todayTrack.name.toLowerCase().trim()}|${todayTrack.artist
      .toLowerCase()
      .trim()}`;
    const yesterdayTrack = yesterdayTracks.get(key);

    if (!yesterdayTrack) {
      // Nueva canción
      changes.push({
        type: "new",
        track: todayTrack,
        change: "nueva",
      });
    } else {
      // Comparar streams
      const todayStreams = parseStreams(todayTrack.streams);
      const yesterdayStreams = parseStreams(yesterdayTrack.streams);
      const difference = todayStreams - yesterdayStreams;

      if (difference > 0) {
        changes.push({
          type: "increase",
          track: todayTrack,
          change: `+${difference.toLocaleString()}`,
          difference: difference,
        });
      } else if (difference < 0) {
        changes.push({
          type: "decrease",
          track: todayTrack,
          change: `${difference.toLocaleString()}`,
          difference: difference,
        });
      } else {
        changes.push({
          type: "no_change",
          track: todayTrack,
          change: "sin cambios",
        });
      }
    }
  });

  return changes;
}

function generateReport(playlistData, changes) {
  const lines = [];
  const playlistId = extractPlaylistId(playlistData.url);

  // Header del reporte
  lines.push(`Reporte de artist.tools`);
  lines.push(`Playlist: ${playlistId}`);
  lines.push(
    `${playlistData.totalTracks || playlistData.tracks.length} tracks | ${
      playlistData.totalStreams || "N/A"
    } total streams`
  );
  lines.push("");

  // Si hay error, reportarlo
  if (playlistData.error) {
    lines.push(`Error: ${playlistData.error}`);
    lines.push("");
    return lines.join("\n");
  }

  lines.push("Cambios desde ayer:");

  // Procesar cambios con el nuevo formato
  changes.forEach((change) => {
    const trackName = change.track.name;
    const artistName = change.track.artist;
    const streams = parseStreams(change.track.streams).toLocaleString();

    if (change.type === "increase") {
      lines.push(
        `${trackName} by ${artistName}: ${streams} streams (${change.change} streams)`
      );
    } else if (change.type === "decrease") {
      lines.push(
        `${trackName} by ${artistName}: ${streams} streams (${change.change} streams)`
      );
    } else if (change.type === "new") {
      lines.push(`${trackName} by ${artistName}: ${streams} streams (nueva)`);
    } else if (change.type === "no_change") {
      lines.push(
        `${trackName} by ${artistName}: ${streams} streams (sin cambios)`
      );
    }
  });

  lines.push(""); // Línea en blanco al final
  return lines.join("\n");
}

// ---- Main ----
(async () => {
  if (!TELEGRAM_BOT_TOKEN || !GITHUB_TOKEN || !CHAT_ID) {
    console.error(
      "Faltan credenciales (TELEGRAM_BOT_TOKEN / GITHUB_TOKEN / CHAT_ID)"
    );
    process.exit(1);
  }

  let urls = [];
  try {
    urls = await getPlaylistsFromGitHub(CHAT_ID);
  } catch (e) {
    await sendToTelegram(
      "Error al leer tu lista: " +
        (e.response?.status === 404 ? "no tienes playlists aún." : e.message)
    );
    process.exit(1);
  }

  if (!urls.length) {
    await sendToTelegram("Tu lista está vacía. Usa /add <url> para agregar.");
    process.exit(0);
  }

  const browser = await chromium.launch();
  const page = await browser.newPage();

  const today = getTodayDate();
  const yesterday = getYesterdayDate();

  // Obtener datos de ayer para comparación
  let yesterdayData = null;
  try {
    yesterdayData = await getDataFromGitHub(CHAT_ID, yesterday);
    console.log("Datos de ayer encontrados:", yesterdayData ? "Sí" : "No");
  } catch (error) {
    console.log("No hay datos de ayer para comparar");
  }

  const todayData = {
    date: today,
    playlists: [],
  };

  const reports = [];
  const summaryData = []; // Array para almacenar datos del resumen

  for (const url of urls) {
    try {
      console.log(`Scraping: ${url}`);
      const playlistData = await scrapePlaylist(page, url);

      // Agregar a los datos de hoy
      todayData.playlists.push(playlistData);

      // Encontrar datos de ayer para esta playlist
      let yesterdayPlaylistData = null;
      if (yesterdayData && yesterdayData.playlists) {
        yesterdayPlaylistData = yesterdayData.playlists.find(
          (p) => p.url === url
        );
        console.log(
          `Datos de ayer para ${url}:`,
          yesterdayPlaylistData ? "Encontrados" : "No encontrados"
        );
      }

      // Comparar y generar reporte
      const changes = compareData(playlistData, yesterdayPlaylistData);
      const report = generateReport(playlistData, changes);
      reports.push(report);

      // Guardar datos para el resumen general
      const playlistId = extractPlaylistId(playlistData.url);
      summaryData.push({
        id: playlistId,
        tracks: playlistData.totalTracks || playlistData.tracks.length,
        streams: playlistData.totalStreams || "N/A",
      });
    } catch (err) {
      console.error(`Error scraping ${url}:`, err);
      reports.push(`Error al procesar: ${url}\n${err.message}`);
    }
  }

  // Guardar datos de hoy en GitHub
  try {
    await saveDataToGitHub(CHAT_ID, today, todayData);
    console.log("Datos guardados en GitHub");
  } catch (error) {
    console.error("Error guardando datos en GitHub:", error);
  }

  // Generar resumen general UNA SOLA VEZ
  let generalSummary = "\nResumen General\n";
  summaryData.forEach((playlist) => {
    generalSummary += `Playlist: ${playlist.id}\n`;
    generalSummary += `${playlist.tracks} tracks | ${playlist.streams} total streams\n\n`;
  });

  // Enviar reportes a Telegram
  const finalReport = reports.join("\n\n") + generalSummary;

  // Dividir el mensaje si es muy largo
  const maxLength = 4000;
  if (finalReport.length <= maxLength) {
    await sendToTelegram(finalReport);
  } else {
    // Dividir en múltiples mensajes
    const chunks = [];
    let currentChunk = "";

    reports.forEach((report) => {
      if ((currentChunk + report).length > maxLength) {
        if (currentChunk) chunks.push(currentChunk);
        currentChunk = report;
      } else {
        if (currentChunk) currentChunk += "\n\n";
        currentChunk += report;
      }
    });

    if (currentChunk) chunks.push(currentChunk);

    // Agregar el resumen general al último chunk o como chunk separado
    if (chunks.length > 0) {
      const lastChunk = chunks[chunks.length - 1];
      if ((lastChunk + generalSummary).length <= maxLength) {
        chunks[chunks.length - 1] = lastChunk + generalSummary;
      } else {
        chunks.push(generalSummary);
      }
    } else {
      chunks.push(generalSummary);
    }

    for (const chunk of chunks) {
      await sendToTelegram(chunk);
      await new Promise((resolve) => setTimeout(resolve, 1000)); // Pausa entre mensajes
    }
  }

  await browser.close();
  console.log("Scraping completado");
})();
