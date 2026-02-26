const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const pino = require('pino');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');

const dataDir = path.resolve(process.env.CRAZY_DATA_DIR || 'data');
const authDir = path.resolve(process.env.CRAZY_WA_AUTH_DIR || 'secrets/wa_auth');
const eventsFile = path.resolve(process.env.CRAZY_WHATSAPP_EVENTS || 'data/whatsapp_events.jsonl');
const outboxFile = path.resolve(process.env.CRAZY_WHATSAPP_OUTBOX || 'data/whatsapp_outbox.jsonl');
const sentLogFile = path.resolve(process.env.CRAZY_WHATSAPP_SENT_LOG || 'data/whatsapp_sent_ids.log');

fs.mkdirSync(dataDir, { recursive: true });
fs.mkdirSync(path.dirname(eventsFile), { recursive: true });
fs.mkdirSync(path.dirname(outboxFile), { recursive: true });

function appendEvent(event) {
  fs.appendFileSync(eventsFile, `${JSON.stringify(event)}\n`, { encoding: 'utf8', mode: 0o600 });
}

function loadSentIds() {
  if (!fs.existsSync(sentLogFile)) return new Set();
  return new Set(fs.readFileSync(sentLogFile, 'utf8').split('\n').map((x) => x.trim()).filter(Boolean));
}

function rememberSent(id) {
  fs.appendFileSync(sentLogFile, `${id}\n`, { encoding: 'utf8', mode: 0o600 });
}

function stableId(obj) {
  return crypto.createHash('sha256').update(JSON.stringify(obj)).digest('hex');
}

async function flushOutbox(sock, sentIds) {
  if (!fs.existsSync(outboxFile)) return;
  const lines = fs.readFileSync(outboxFile, 'utf8').split('\n').slice(-500);
  for (const line of lines) {
    const clean = line.trim();
    if (!clean) continue;
    let item;
    try {
      item = JSON.parse(clean);
    } catch {
      continue;
    }
    const id = item.id || stableId(item);
    if (sentIds.has(id)) continue;
    if (!item.chatId || !item.text) continue;
    await sock.sendMessage(item.chatId, { text: String(item.text).slice(0, 3000) });
    sentIds.add(id);
    rememberSent(id);
  }
}

async function connect() {
  const sentIds = loadSentIds();
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();
  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: 'silent' })
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      const body = msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
      appendEvent({
        type: 'message',
        id: msg.key.id,
        chatId: msg.key.remoteJid,
        sender: msg.pushName || msg.key.participant || msg.key.remoteJid,
        body,
        unread: !msg.key.fromMe,
        starred: false,
        timestamp: new Date((msg.messageTimestamp || Date.now()/1000) * 1000).toISOString()
      });
    }
  });

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('Scan QR in terminal to connect WhatsApp.');
      console.log(qr);
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code !== DisconnectReason.loggedOut) {
        console.log('WhatsApp disconnected. Reconnecting...');
        connect().catch(console.error);
      } else {
        console.log('WhatsApp logged out. Delete secrets/wa_auth and reconnect.');
      }
    }
    if (connection === 'open') {
      console.log('WhatsApp bridge connected.');
    }
  });

  setInterval(() => {
    flushOutbox(sock, sentIds).catch((err) => console.error('Outbox flush failed:', err.message));
  }, 5000);
}

connect().catch((err) => {
  console.error('Bridge crashed', err);
  process.exit(1);
});
